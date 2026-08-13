-- 靶機 DB 種子（票 #44）—— 真攻擊鏈「SQLi 撈憑證 → 憑證開 DB → flag」的資料層。
--
-- 這份檔烤進 golden（進 golden_stamp）：結構是靜態的，改結構就該重烤。
-- **flag 的值不在這裡**：seed 只放一個 placeholder，真 flag 由 range-up 產生、
-- cloud-init 注入、開機時 inject-flag.sh 以 UPDATE 寫入（票 #45）。改 flag 不重烤。
--
-- 兩跳靠 **MySQL 授權邊界**強制，不是靠 app 邏輯：
--   webapp@localhost  → app 用的連線帳號。SELECT 僅限 shopdb.*（含 credentials）。
--                        SQLi 的 UNION 只能撈到這個帳號 grant 得到的表 → 撈得到憑證，
--                        **撈不到 vault.flag**（沒有該 database 的 grant）。
--   dbadmin@'%'        → 憑證表裡那組帳密。可從 VLAN30 遠端連 :3306（host '%'），
--                        且是唯一 grant 得到 vault.* 的帳號 → 第二道門的鑰匙。
--
-- 為什麼 SQLi 撈不到 flag：webapp 對 vault 沒有任何 grant，UNION 到 vault.flag 會被
-- MySQL 以權限拒絕。紅隊非得先撈 dbadmin 帳密、再以 dbadmin 重連不可 —— 這就是
-- spec §3.2「內容鏈接：前一關的產出是後一關的鑰匙」，由授權邊界落地。

-- ── 商品目錄：SQLi 的受攻擊面（webapp 讀得到，公開）───────────────────────
CREATE DATABASE IF NOT EXISTS shopdb CHARACTER SET utf8mb4;
USE shopdb;

CREATE TABLE IF NOT EXISTS products (
  id          INT PRIMARY KEY,
  name        VARCHAR(128) NOT NULL,
  price       INT NOT NULL,
  description VARCHAR(512) NOT NULL
);
INSERT INTO products (id, name, price, description) VALUES
  (1, 'Range Runner Boots', 2400, 'All-terrain boots for the blue team.'),
  (2, 'Falco Field Cap',    600,  'Keeps the sun off while you read syscalls.'),
  (3, 'OVS Patch Cable',    150,  'Two metres, VLAN-tagged, cerulean.')
ON DUPLICATE KEY UPDATE name = VALUES(name);

-- ── 憑證表：SQLi 的戰利品（webapp 也讀得到 —— 這正是壞 app 用單一寬帳號的代價）──
-- dbadmin 的密碼放這裡。紅隊 UNION 撈出它，才有第二跳的鑰匙。
CREATE TABLE IF NOT EXISTS credentials (
  id       INT PRIMARY KEY,
  service  VARCHAR(64) NOT NULL,
  username VARCHAR(64) NOT NULL,
  password VARCHAR(128) NOT NULL
);
INSERT INTO credentials (id, service, username, password) VALUES
  (1, 'mysql-admin', 'dbadmin', 'D0-n0t-sh1p-th1s-Cred-2026')
ON DUPLICATE KEY UPDATE password = VALUES(password);

-- ── flag 保險庫：獨立 database，webapp 沒有任何 grant ─────────────────────
-- flag 值由開機注入（inject-flag.sh），seed 只建表 + placeholder。
CREATE DATABASE IF NOT EXISTS vault CHARACTER SET utf8mb4;
CREATE TABLE IF NOT EXISTS vault.flag (
  id    INT PRIMARY KEY,
  token VARCHAR(128) NOT NULL
);
INSERT INTO vault.flag (id, token) VALUES
  (1, 'flag{PLACEHOLDER_REPLACED_AT_BOOT_BY_inject-flag}')
ON DUPLICATE KEY UPDATE token = token;   -- 保留現值，不覆蓋注入後的真 flag

-- ── 授權邊界（攻擊鏈的實際 enforcement）──────────────────────────────────
-- app 帳號：只在本機、只讀 shopdb。刻意不給 vault 任何權限。
CREATE USER IF NOT EXISTS 'webapp'@'localhost' IDENTIFIED BY 'webapp-local-only';
GRANT SELECT ON shopdb.* TO 'webapp'@'localhost';

-- 第二道門：憑證表裡那組帳密，可從 VLAN30 遠端連，且是唯一讀得到 flag 的帳號。
CREATE USER IF NOT EXISTS 'dbadmin'@'%' IDENTIFIED BY 'D0-n0t-sh1p-th1s-Cred-2026';
GRANT SELECT ON vault.* TO 'dbadmin'@'%';
GRANT SELECT ON shopdb.* TO 'dbadmin'@'%';

FLUSH PRIVILEGES;
