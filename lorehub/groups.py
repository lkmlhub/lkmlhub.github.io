"""Subsystem groups (the 'language colour' on the home page), guessed from list names."""

import re

GROUPS = [
    ("Releases", "#8957e5", r"^(stable|stable-rc|stable-commits|linux-next|regressions|linux-kernel-announce|linux-announce|linux-cve-announce|stable-rt)$"),
    ("Networking", "#1f6feb", r"netdev|netfilter|lvs|wireless|bluetooth|mptcp|rdma|bridge|batman|dccp|ath\d+|ath9k|brcm80211|b43|wireguard|openvswitch|ovs-|wpan|linux-can|^can$|sctp|xdp|dpdk|connman|iproute|ethtool|netem|lartc|linux-net|wpa|hostap|iwd|ell$|nftables|bpfilter|tipc|ipvs|intel-wired-lan|quic|openvpn|kernel-tls|linux-x25|linux-ppp|linux-hams|radiotap|vhost-user"),
    ("Filesystems", "#d29922", r"fsdevel|ext4|btrfs|xfs|f2fs|nfs|ceph|cifs|samba|ntfs|erofs|squashfs|jfs|ocfs|gfs|cluster|autofs|unionfs|overlayfs|fsverity|fscrypt|linux-mtd|lustre|nilfs|reiserfs|bcachefs|9p|fuse|ubifs|linux-fsd|davfs|ecryptfs|exfat|udf|afs|orangefs|netfs|v9fs"),
    ("Storage", "#bf8700", r"scsi|nvme|linux-block|^dm-|dm-devel|raid|iscsi|target-devel|linux-ide|linux-ata|linux-mmc|bcache|linux-btrace|ublk|linux-nvdimm|lvm|cryptsetup|dm-crypt|drbd|ceph-devel|stratis|multipath"),
    ("Graphics and media", "#da3633", r"dri-|dri$|gfx|nouveau|freedreno|radeon|linux-media|wayland|mesa|xorg|panfrost|etnaviv|virtio-gpu|intel-xe|linux-fbdev|linuxtv|kms|asahi|amd-gfx|intel-gfx|igt-dev"),
    ("Architectures", "#238636", r"arm|riscv|s390|powerpc|ppc|mips|x86|loongarch|parisc|sparc|xtensa|openrisc|linux-sh|m68k|alpha|ia64|csky|hexagon|linux-arch|snps-arc|^arc|opensbi|sophgo|spacemit|linux-um|uml|microblaze|nios2|apple|kvmarm|linux-oxnas|linux-amlogic|linux-rockchip|linux-sunxi|linux-tegra|linux-samsung|linux-omap|linux-imx|linux-mediatek|linux-stm32|linux-renesas|linux-msm|linux-snapdragon|linux-aspeed|openbmc|linux-sgx|u-boot|barebox"),
    ("Virtualization", "#3fb950", r"kvm|qemu|virtualization|xen|hyperv|iommu|vfio|containers|criu|lxc|coconut|svsm|virtio|linux-coco|firecracker|libvir|crosvm|virtiofs|sev|tdx"),
    ("Security", "#f85149", r"selinux|security|integrity|audit|apparmor|keyrings|tpm|hardening|oss-security|landlock|linux-sgx|ima|smack|tomoyo|lsm|crypto|cryptoapi|linux-crypto|fips|sbat|ima"),
    ("BPF, tracing and testing", "#a371f7", r"bpf|trace|perf|ltp|kasan|kunit|kselftest|dwarves|debuggers|toolchains|llvm|gcc|ftrace|kdump|crash|kexec|syzkaller|linux-test|lkp|kernelci|kdevops|fstests|coccinelle|cocci|sparse|smatch|tools"),
    ("Hardware and drivers", "#0e8a7d", r"imx|mfd|ntb|nova-gpu|phone-devel|linux-console|linux-hotplug|linuxpps|usb|pci|input|hid|i2c|spi|gpio|pwm|iio|sound|alsa|asoc|serial|tty|acpi|power|thermal|hwmon|watchdog|clk|devicetree|^dt|platform-driver|pinctrl|regulator|leds|rtc|phy|edac|nvdimm|cxl|fpga|firmware|efi|pm$|linux-pm|cpufreq|cpuidle|mailbox|rproc|remoteproc|soundwire|sdio|bluez|linux-i3c|linux-pcmcia|libata|linux-ide|linux-mips|chrome-platform|platform-driver-x86|linux-acpi|linux-hwmon|linux-iio|linux-input|linux-serial|linux-spi|linux-gpio|linux-kbuild|linux-clk|linux-rtc|linux-phy|linux-edac|accel|asahi|dmaengine|linux-usb|linux-pci|linux-crypto|memory|numa|linux-kernel-mentees|greybus|staging|linux-doc|parport|cpufreq|dmi|nfc|libnvdimm|linux-fpga"),
    ("Kernel core", "#f1e05a", r"kernel-janitors|^rcu$|lkmm|sched-ext|live-patching|printk|driver-core|kernel-testers|linux-smp|multikernel|^lkml$|^linux-mm$|^linux-kernel|^linux-api$|^linux-rt|cgroups|damon|linux-hardening|linux-mm|linux-perf|rust-for-linux|linux-modules|linux-kbuild|linux-kernel-mentees|linux-fsdevel|linux-man|linux-doc|linux-sparse|workflows|ksummit|tech-board|linux-tip|^kernelnewbies|outreachy|linux-trace"),
    ("Userland and tools", "#6e7681", r"^git$|util-linux|buildroot|yocto|bitbake|openembedded|dash|ccan|cryptsetup|dracut|kmod|^b4|bluez|systemd|backports|distributions|diamon|devicetree-compiler|devicetree-spec|dpdk|barebox|connman|criu|libc|glibc|musl|busybox|coreboot|grub|syslinux|initramfs|mdadm|e2fsprogs|xfsprogs|linux-btrfs|iproute2|sparse|patchwork|ltp|cti-tac|lm-sensors"),
]
COMPILED = [(n, c, re.compile(rx)) for n, c, rx in GROUPS]
OTHER = ("Other", "#8b949e")


def group_for(name):
    for gname, color, rx in COMPILED:
        if rx.search(name):
            return gname
    return OTHER[0]


def group_colors():
    d = {n: c for n, c, _ in GROUPS}
    d[OTHER[0]] = OTHER[1]
    return d
