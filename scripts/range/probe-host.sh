#!/usr/bin/env bash
# 票 #13 Slice 2 前置 —— 探測大主機能力（唯讀，不改系統）。
#
# 決定四件事：能不能開真 VM（nested KVM）、用不用 libvirt、OVS/nft 是否就緒、
# Falco 走哪種 eBPF probe（看 kernel BTF）、以及能開幾台 VM（CPU/RAM/磁碟）。
# 用法：sudo bash scripts/range/probe-host.sh   然後把整段輸出貼回。
set -uo pipefail

echo "=== OS / kernel ==="
uname -a
if [ -f /etc/os-release ]; then . /etc/os-release; echo "$PRETTY_NAME"; fi
echo

echo "=== CPU 虛擬化 (VT-x / AMD-V) ==="
n=$(grep -Ec '(vmx|svm)' /proc/cpuinfo || true)
if [ "${n:-0}" -gt 0 ]; then echo "支援（$n 個邏輯核帶 vmx/svm）"; else echo "不支援 —— 開不了硬體加速 VM"; fi
echo

echo "=== KVM / nested ==="
if [ -e /dev/kvm ]; then echo "/dev/kvm 存在"; else echo "/dev/kvm 不存在"; fi
for f in /sys/module/kvm_intel/parameters/nested /sys/module/kvm_amd/parameters/nested; do
  [ -f "$f" ] && echo "nested($f) = $(cat "$f")"
done
if command -v kvm-ok >/dev/null 2>&1; then kvm-ok || true; else echo "（無 kvm-ok；可 apt install cpu-checker）"; fi
echo

echo "=== libvirt / virsh ==="
if command -v virsh >/dev/null 2>&1; then virsh --version; virsh list --all 2>/dev/null | head; else echo "未安裝 libvirt/virsh"; fi
echo

echo "=== Open vSwitch ==="
if command -v ovs-vsctl >/dev/null 2>&1; then ovs-vsctl --version | head -1; else echo "未安裝 openvswitch"; fi
echo

echo "=== nftables ==="
if command -v nft >/dev/null 2>&1; then nft --version; else echo "未安裝 nftables"; fi
echo

echo "=== Falco eBPF 前置（kernel BTF / modern-bpf 條件）==="
if [ -f /sys/kernel/btf/vmlinux ]; then echo "BTF 存在 → modern eBPF (CO-RE) 可用"; else echo "無 BTF → modern-bpf 可能要換 legacy probe / kernel module"; fi
echo "kernel: $(uname -r)"
if command -v falco >/dev/null 2>&1; then falco --version; else echo "（Falco 尚未安裝，之後裝；這裡先確認 kernel 條件）"; fi
echo

echo "=== 資源（決定能開幾台 VM）==="
echo "CPU 核數: $(nproc)"
echo "--- 記憶體 ---"; free -h
echo "--- 根磁碟 ---"; df -h /
echo

echo "=== 探測完成，把以上整段貼回 ==="
