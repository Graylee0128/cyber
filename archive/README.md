# cyber/archive — 已非現行的拓樸圖

現行拓樸只有一張：[../demo_network_topology_v0_2_1.svg](../demo_network_topology_v0_2_1.svg)。
本目錄的圖**不是廢棄**，是不再作為施工依據的世代或參考。留著各有理由。

| 檔案 | 世代 | 為什麼留著 | 為什麼不是現行 |
|---|---|---|---|
| [demo_network_topology_kali_nat_webserver.svg](./demo_network_topology_kali_nat_webserver.svg) | G0 · 思考過程產物 | 記錄最初的單一平面設計，說明為什麼後來要做網段隔離 | 無觀測平面，紫隊在圖上沒有位置；NAT 讓六台 kali 的來源 IP SNAT 成兩個主機 IP，事件無法歸屬到個別攻擊者 |
| [demo_network_topology_v0_2.svg](./demo_network_topology_v0_2.svg) | G1 · 三區隔離 | v0.2.1 的直接前身；三區切法與四條紫隊契約都出自這張 | Battleboard／Portal／Range Core 放在 Z-MGMT，與同圖政策 `RED → MGMT: deny all` 直接衝突——紅隊連不到自己的 Player Portal |
| [v3_k8s_topology.svg](./v3_k8s_topology.svg) | G3 · 之後擴展考量 | 遠期參考。已定調「靶機與攻擊機不加入 cluster，kubelet :10250 與 kube-apiserver :6443 永不出現在攻擊面」，這條是安全決策，未來要保留 | 目前不施工。per-tenant VLAN pair 與 provisioner 模式尚未有需求 |

## 世代關係

```text
G0  kali_nat_webserver     單一平面 · 無觀測平面
      ↓ 加入網段隔離
G1  v0_2                   三區 · 四條紫隊契約成形
      ↓ 拆出產品平面，修正 Portal 位置衝突
G2  v0_2_1                 四區 ← 現行
      ↓ （尚未施工）
G3  v3_k8s                 k8s 控制平面 + per-tenant range
```

四條紫隊契約自 G1 起跨世代不變，是「拓樸可退可換、紫隊不必重做」的依據：

1. 三個 port：`:3100` Loki ／ `:9090` Prometheus ／ `:4317` OTLP gRPC
2. `TARGET → MGMT` 單向
3. `RED → MGMT` deny all
4. collector（Alloy／Falco）裝在 target 側，不在 mgmt 側

契約條文寫在 [SA §12](../資安攻防平台_系統架構設計文件_v0.1.md)。
