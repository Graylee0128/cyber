**現在這個 workshop 技術上有價值，但展示體驗很差。** 目前比較像工程師的 integration test，不像一個「看得懂發生什麼事」的完整 Demo。

現在流程其實是：

```
```

```
curl SQLi
   ↓
App log
   ↓
Loki
   ↓
Grafana Alert
   ↓
Webhook
   ↓
Receiver
   ↓
ipset / iptables
   ↓
curl timeout
```

除了 Grafana Alerting 頁面，中間幾乎全部都靠 terminal 驗證：

```
```

```
docker compose logs ...
curl /bans
curl /search
```

這對學底層機制很好，但拿來 Demo 確實不直觀。

我會建議你把它改成 **Grafana SOC-lite Dashboard**，至少放 4 個區塊：

```
```

```
┌──────────────────────────────────────────────┐
│ SQL Injection Response Dashboard             │
├───────────────────┬──────────────────────────┤
│ SQLi Events       │ Alert Status             │
│ 27 / 5 min        │ 🔴 FIRING                │
├───────────────────┼──────────────────────────┤
│ Attacker IP       │ Response Action          │
│ 172.28.0.1        │ 🔒 BLOCKED               │
│                   │ TTL: 52 sec              │
├───────────────────┴──────────────────────────┤
│ Timeline                                     │
│ 14:20:01 SQLi detected                       │
│ 14:20:03 Alert firing                        │
│ 14:20:03 Webhook sent                        │
│ 14:20:04 IP blocked                          │
└──────────────────────────────────────────────┘
```

這樣使用者只需要做一次：

```
```

```
curl -G http://localhost:8022/search \
  --data-urlencode "q=' OR '1'='1"
```

甚至之後再加一個簡單 Web UI：

```
```

```
Search:
[ ' OR '1'='1                  ] [Send]

Result:
❌ SQL Error Detected

Security Status:
🔴 Suspicious request
🟠 Grafana Alert firing
🔒 Source IP blocked for 60s
```

然後旁邊 Grafana dashboard 即時變化。

對你這個 repo，我會把定位拆成兩層：

```
```

```
Layer 1：Engineering Lab
curl / logs / ipset / iptables
→ 證明底層真的工作

Layer 2：Portfolio Demo
Grafana Dashboard / Attack UI
→ 讓面試官 30 秒看懂價值
```

**底層 CLI 不該刪掉**，因為那才證明你不是做假 UI；但現在確實缺一層 presentation layer。

尤其這個專案如果最後要拿來展示，`SQLi → Detection → Alert → Automated Response` 本身其實很適合做 dashboard。比單純秀 Prometheus CPU graph 更有作品辨識度。

所以SOC >> 只要給 藍+紫對看? 那web-UI呢?

對，建議不要把「SOC Dashboard」跟「整個產品 Web UI」混成同一個東西。

### SOC Dashboard：主要給藍隊＋紫隊

它的定位是 **defensive operations console**。

```
```

```
                ┌─────────────┐
Red Team ──────▶│ Target Apps │
                └──────┬──────┘
                       │ logs / metrics / security events
                       ▼
              Loki / Prometheus / Falco
                       │
                       ▼
                 Grafana SOC
                  ▲         ▲
                  │         │
               Blue       Purple
```

藍隊需要看：

-  Attack events 
-  Source IP 
-  SQLi / shell / sensitive file access 
-  Alert `Pending / Firing / Resolved` 
-  Firewall / ipset block 
-  Container health 
-  Timeline 

紫隊則可以看得更多，例如攻防雙方事件、rule 是否命中、MTTD / MTTR、攻擊是否被成功阻擋等。

**紅隊原則上不要看到 SOC Dashboard。** 不然紅隊打一個 payload，馬上看到「Falco detected」或「IP blocked」，等於洩漏防守資訊。

---

### 那 Web UI 是什麼？

Web UI 應該是整個 Cyber Range / 攻防遊戲的 **Game Portal**。

例如：

```
```

```
http://lab.example.com

┌────────────────────────────────────────┐
│        Cyber Range Platform            │
│                                        │
│  Player: Gary                          │
│  Team: Red                             │
│  Scenario: SQL Injection               │
│                                        │
│  Mission                               │
│  ─────────────────────                 │
│  找到 Web Application 的弱點           │
│                                        │
│  Target                                │
│  http://target.lab                     │
│                                        │
│  Difficulty                            │
│  ● Easy  ○ Normal  ○ Hard              │
│                                        │
│  [ Start Challenge ]                   │
└────────────────────────────────────────┘
```

登入後根據 Role 顯示不同頁面。

| RoleWeb UI 看到什麼  |                                                    |
| ---------------- | -------------------------------------------------- |
| 🔴 Red           | 任務、Target、提示、Flag、攻擊結果                             |
| 🔵 Blue          | Incident、Alert、SOC Dashboard、response action       |
| 🟣 Purple        | Red + Blue timeline、attack mapping、MITRE ATT&CK、評分 |
| 👨‍🏫 Instructor | 全部資訊、開關 scenario、reset、scoreboard                  |

這才是完整產品。

---

### 你現在的 SQLi Lab 就可以變成這樣

紅隊根本不需要再手打：

```
```

```
curl -G http://localhost:8022/search \
  --data-urlencode "q=' OR '1'='1"
```

做一個簡單的 vulnerable Web UI：

```
```

```
┌───────────────────────────────┐
│ Employee Search               │
│                               │
│ Search                        │
│ [' OR '1'='1____________] 🔍 │
│                               │
│ Internal Server Error         │
└───────────────────────────────┘
```

紅隊是在「正常網站」裡自己找漏洞。

背後：

```
```

```
Red browser
    ↓
Vulnerable Web UI
    ↓
GET /search?q=' OR '1'='1
    ↓
SQL Error
    ↓
structured log
    ↓
Alloy → Loki
    ↓
Grafana Alert
    ↓
Webhook
    ↓
Receiver
    ↓
ipset
    ↓
Red Team connection DROP
```

此時藍隊 SOC 畫面可能突然：

```
```

```
🔴 SQL Injection Burst

Source
172.28.0.1

Events
██████████ 8 requests

Detection
SQLInjectionBurst

Response
✅ IP automatically blocked

TTL
53 sec
```

這樣才有「攻防」的感覺。

---

### 我會把整個 UI 分成三層

```
```

```
                    Cyber Range
                         │
        ┌────────────────┼────────────────┐
        │                │                │
        ▼                ▼                ▼
   Game Portal       Target Web       SOC Console
 React / Next.js      Vulnerable        Grafana
        │                App              │
        │                 │               │
   玩家/分隊/關卡      Red 攻擊           Blue/Purple
   Score/Hint          的地方            監控/Response
```

其中 **Web UI 不應該只是 Grafana**。

Grafana 是 observability / SOC component；Game Portal 才是你整個產品的 frontend。

甚至可以做：

```
```

```
/                    → Login
/game                → Mission
/game/target         → Target information
/scoreboard          → Score

/red                  → Red console
/blue                 → Blue operations
/purple               → Purple analysis

/admin                → Instructor
```

這會讓你現在「一堆 curl + Docker + Grafana」的 Lab，真正開始變成一個 **Cyber Range Platform**，而不是單純 observability demo。

而且紅隊操作 Target Web、藍隊看 SOC、紫隊看全局，角色資訊不對稱本身就是遊戲設計的一部分。

如果我還又第三個畫面 >> 給大家看目前紅藍對抗的進度? [ maybe like MTTACK? ] 給我規畫建議

對，我會建議你**一定做第三個「共享戰況畫面」**。而且它不應只是把 Grafana 投到大螢幕，而應該是一個獨立的 **Battleboard / Match Overview / War Room Dashboard**。

核心差異是：

```
```

```
Red UI       → 我要怎麼進攻？
Blue SOC     → 我要怎麼偵測與處置？
Battleboard  → 現在整場攻防打到哪裡了？
```

這三個畫面的 audience 完全不同。

## 我建議的三層 UI

```
```

```
                    Cyber Range Platform
                            │
          ┌─────────────────┼─────────────────┐
          │                 │                 │
          ▼                 ▼                 ▼
      Red / Player        Blue SOC        Battleboard
        Portal             Console           公開
          │                 │                 │
      任務 / Target       Alert / Logs      比賽進度
      Hint / Flag         Incident          Score
      Attack             Response          ATT&CK
                         Forensics         Timeline
```

其中第三個可以直接叫：

> **Live Battleboard**

放教室投影幕、大電視，所有紅藍紫隊都能看。

---

# Battleboard 不要只做 Scoreboard

單純：

```
```

```
Red  750
Blue 620
```

其實很無聊。

最好做成「**戰況可視化**」。

例如：

```
```

```
┌─────────────────────────────────────────────────────────────┐
│ 🔴 RED vs 🔵 BLUE              ROUND 01       38:21 LEFT   │
├──────────────────┬─────────────────────┬────────────────────┤
│ SCORE            │ ATTACK PROGRESS     │ DEFENSE STATUS     │
│                  │                     │                    │
│ RED   720        │ Recon        ██████ │ Detection   82%    │
│ BLUE  680        │ InitialAccess ████   │ Blocked     12     │
│                  │ Execution     ██     │ Incidents    4     │
├──────────────────┴─────────────────────┴────────────────────┤
│                    MITRE ATT&CK                              │
│                                                             │
│ Recon        Initial Access      Execution      Persistence │
│  ● T1595       🔴 T1190            🟡 T1059        ○          │
│                                                             │
│ PrivEsc      Credential Access   Discovery       C2         │
│  ○             ○                  🟢 T1087        ○          │
├─────────────────────────────────────────────────────────────┤
│ LIVE TIMELINE                                               │
│                                                             │
│ 14:30:12 🔴 Attack activity detected                        │
│ 14:30:18 🔵 Detection triggered                             │
│ 14:30:22 🔵 Automated response executed                     │
│ 14:30:30 🏆 Blue Team +50                                   │
└─────────────────────────────────────────────────────────────┘
```

這就會開始很像真正的 **Cyber Range / CTF + SOC Simulation**。

---

# MITRE ATT&CK 很適合，但不要直接照搬整張 Matrix

完整 Enterprise ATT&CK 太大，例如：

```
```

```
Reconnaissance
Resource Development
Initial Access
Execution
Persistence
Privilege Escalation
Defense Evasion
Credential Access
Discovery
Lateral Movement
Collection
Command and Control
Exfiltration
Impact
```

如果全部塞進去，玩家會看不懂。

你的產品比較適合：

### Scenario-based ATT&CK

這一關只顯示可能涉及的 technique。

例如 SQL Injection：

```
```

```
Initial Access
└─ T1190 Exploit Public-Facing Application
       ↓
Execution
└─ Web Application Exploitation
       ↓
Credential Access
└─ DB credential exposure
       ↓
Collection
└─ Database data
       ↓
Exfiltration
└─ Data extraction
```

然後 Battleboard 即時亮燈：

```
```

```
○ 未發生
🟡 Attempted
🔴 Red Success
🟢 Blue Detected / Blocked
```

這個設計會比「目前 SQLi 發生 8 次」高級非常多。

---

# 更重要：Battleboard 不能洩漏情報

這是你設計時最容易踩的坑。

例如如果所有人都看到：

```
```

```
Source IP: 172.28.0.4
Rule: SQLInjectionBurst
Threshold: > 4 requests / 1m
iptables blocked
TTL: 60 sec
```

紅隊馬上就知道：

> 喔，四次會被擋，而且只 ban 60 秒。

整場遊戲就被破壞了。

所以你需要做 **Information Sanitization**。

### Blue SOC 可以看到

```
```

```
172.28.0.4
SQLInjectionBurst
4 events / 1m
ipset ban
TTL 60
payload = ' OR 1=1
```

### 公開 Battleboard 只能看到

```
```

```
14:32

Attack activity detected
Initial Access

Technique:
T1190 Exploit Public-Facing Application

Defense:
Detection triggered

Status:
CONTAINED
```

也就是：

```
```

```
Raw Security Event
       ↓
Normalization
       ↓
Sanitization
       ↓
Public Battle Event
```

這個概念很重要。

---

# 我會把戰況分成 5 個核心 Widget

### ① Match Header

```
```

```
RED TEAM            BLUE TEAM

  720       VS        680

Round 2 / 3
38:21 remaining
```

最直覺。

---

### ② Attack Chain Progress

比 ATT&CK Matrix 更容易讓一般玩家理解：

```
```

```
Recon
  ↓
Initial Access
  ↓
Execution
  ↓
Privilege Escalation
  ↓
Lateral Movement
  ↓
Objective
```

顏色：

```
```

```
○ Not reached
🟡 In progress
🔴 Red succeeded
🟢 Blue stopped
```

例如：

```
```

```
Recon             🔴
  ↓
Initial Access    🔴
  ↓
Execution         🟢 BLOCKED
  ↓
Privilege Esc.    ○
  ↓
Objective         ○
```

大家一眼就知道：

> 紅隊已經進去了，但是在 Execution 被藍隊攔下。

---

### ③ MITRE ATT&CK Coverage

這個比較專業。

例如：

| TechniqueRedBlue                 |   |            |
| -------------------------------- | - | ---------- |
| T1190 Exploit Public-Facing App  | ✅ | ✅ Detected |
| T1059 Command Interpreter        | ✅ | ❌ Missed   |
| T1087 Account Discovery          | ✅ | ✅ Detected |
| T1071 Application Layer Protocol | ⏳ | —          |

這對紫隊尤其有價值。

因為 Purple Team 可以開始討論：

> 「T1059 打成功了，但 Detection Rule 沒有 coverage。」

這就不只是遊戲，而開始像真正的 **Detection Engineering**。

---

### ④ Live Timeline

我很推薦。

```
```

```
14:31:02  🔴 Offensive activity
14:31:08  ⚔️ Initial Access attempted

14:31:10  🔵 Detection triggered
14:31:15  🛡 Response executed

14:31:17  ✅ Attack contained

14:32:03  🔴 New attack activity
```

甚至可以做成：

```
```

```
RED ─────●────────●─────────────●──────
         │        │             │
         │       Alert          │
         │        │             │
BLUE ─────────────●──────●──────────────
                       Block
```

這個非常適合投影幕。

---

### ⑤ Objectives / Score

不要只算 attack 次數。

可以設計：

```
```

```
Red Objectives
☑ Discover endpoint         +50
☑ Initial Access           +100
☐ Privilege Escalation     +200
☐ Capture Flag             +500

Blue Objectives
☑ Detect Initial Access    +100
☑ Respond < 60 sec         +150
☐ Identify technique       +100
☑ Contain attacker         +200
```

這樣 scoring 才會跟實際資安能力連結。

---

# Purple Team 可以再有特殊模式

Purple Team 不一定只看 Battleboard。

我甚至會設計：

```
```

```
Public Battleboard
        │
        ▼
Purple Analysis Mode
```

Purple 能點：

```
```

```
T1190
```

進去看到：

```
```

```
Attack
──────
Technique: T1190
Timestamp: 14:31:04

Telemetry
──────
Application Log ✅
WAF               ❌
Falco             ❌
Loki              ✅

Detection
──────
SQLInjectionBurst
Detection latency: 8.2s

Response
──────
Webhook
↓
ipset
↓
DROP

MTTD: 8.2 sec
MTTR: 14.5 sec
```

這就很接近真正 Purple Team Exercise。

---

# 甚至可以加入「攻防 KPI」

這會讓你的平台變得更專業。

例如：

```
```

```
MTTD
Mean Time To Detect
8.3 sec

MTTR
Mean Time To Respond
14.2 sec

Detection Rate
82%

Containment Rate
71%

Attack Success Rate
34%

ATT&CK Coverage
6 / 9 techniques
```

這些數據最後還可以產生成績報告：

```
```

```
Exercise Report
───────────────

Red
Attack Success       67%
Objectives            4/7

Blue
Detection Rate       82%
MTTD                  8.3 sec
MTTR                 14.2 sec

Coverage gaps
T1059
T1071

Recommended improvements
...
```

這就已經不是單純 CTF 了。

---

# 技術架構也建議分開

不要讓 Battleboard 直接去抓：

```
```

```
Loki
Prometheus
Grafana API
Falco logs
```

然後自己亂拼。

長期最好建立一個：

> **Game Event Service**

```
```

```
             Red App
                │
Falco ──────────┤
                │
Grafana Alert ──┤
                │
Blue Action ────┤
                ▼
        Game Event Service
                │
        normalize events
                │
          PostgreSQL
          Redis / PubSub
                │
         WebSocket / SSE
                │
       ┌────────┴────────┐
       ▼                 ▼
 Battleboard          Score Engine
```

統一 event 例如：

```
```

```
{
  "type": "attack.detected",
  "team": "red",
  "scenario": "sqli-01",
  "technique": "T1190",
  "timestamp": "...",
  "visibility": "public"
}
```

另一個：

```
```

```
{
  "type": "response.block",
  "team": "blue",
  "technique": "T1190",
  "timestamp": "...",
  "visibility": "blue"
}
```

這個 `visibility` 會非常重要：

```
```

```
public
red
blue
purple
instructor
```

同一套 event system 就可以控制誰看到什麼。

---

# 最終我會規劃成四種 View

其實做到最後不是三個，而是四種角色 View 最合理：

| ViewAudience用途        |            |                              |
| --------------------- | ---------- | ---------------------------- |
| 🎮 Player Portal      | Red / Blue | 任務、提示、Flag、Score             |
| 🛡 SOC Console        | Blue       | Logs、Alert、Incident、Response |
| ⚔️ Live Battleboard   | 所有人        | 戰況、Score、ATT&CK、Timeline     |
| 🎛 Instructor Console | 老師         | 控制遊戲、Reset、Inject Event、全局監控 |

其中：

> **Battleboard 是「觀賽介面」，不是操作介面。**

我認為這點是整個產品 UX 最重要的定位。

---

## 如果照 MVP → V2 → V3 做

我不建議現在一次做太大。

**MVP** 先做 Battleboard：Red/Blue Score、Attack Chain、最近 10 個事件、Current Alert、Round Timer。

**V2** 再加入 MITRE ATT&CK mapping、MTTD / MTTR、Detection Rate、WebSocket 即時更新。

**V3** 才做完整 Cyber Range：Role-based UI、Instructor Console、多 Scenario、多租戶、Replay、Exercise Report、AI Purple-Team Coach。

你現在已經有 SQLi → Loki → Grafana → Webhook → ipset 這條底層鏈路，所以其實下一個最值得做的，不是再塞另一個 security tool，而是建立 **Game Event Layer + Live Battleboard**。這會把目前「很多 observability / security lab 拼在一起」真正提升成一個有產品感的攻防平台。