#!/bin/bash -e
# pi-gen: diese Stufe baut auf stage2 (Raspberry Pi OS Lite) auf
if [ ! -d "${ROOTFS_DIR}" ]; then
  copy_previous
fi
