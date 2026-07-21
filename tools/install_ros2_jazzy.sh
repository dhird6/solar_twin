#!/usr/bin/env bash
# Install ROS 2 Jazzy Jalisco on Ubuntu 24.04 (noble), aarch64.
#
# Jazzy is the native ROS 2 distro for 24.04, and the installed Isaac Sim
# 6.0.1-rc.7 bridge bundles jazzy internal libs (exts/isaacsim.ros2.core/jazzy),
# so it matches the sim. Run with sudo:
#
#     sudo bash tools/install_ros2_jazzy.sh
#
# Idempotent-ish: safe to re-run. Uses the current ros-apt-source .deb method.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Please run with sudo: sudo bash tools/install_ros2_jazzy.sh" >&2
  exit 1
fi

CODENAME="$(. /etc/os-release && echo "$VERSION_CODENAME")"
if [[ "$CODENAME" != "noble" ]]; then
  echo "WARNING: expected Ubuntu 24.04 (noble), found '$CODENAME'. Jazzy targets noble." >&2
fi

echo "==> Locale (UTF-8)"
apt-get update
apt-get install -y locales
locale-gen en_US en_US.UTF-8
update-locale LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8

echo "==> Enable universe + prerequisites"
apt-get install -y software-properties-common curl
add-apt-repository -y universe

echo "==> Add ROS 2 apt source (ros-apt-source .deb)"
ROS_APT_SOURCE_VERSION="$(curl -s https://api.github.com/repos/ros-infrastructure/ros-apt-source/releases/latest \
  | grep -F '"tag_name"' | awk -F'"' '{print $4}')"
echo "    ros-apt-source version: ${ROS_APT_SOURCE_VERSION}"
curl -L -o /tmp/ros2-apt-source.deb \
  "https://github.com/ros-infrastructure/ros-apt-source/releases/download/${ROS_APT_SOURCE_VERSION}/ros2-apt-source_${ROS_APT_SOURCE_VERSION}.${CODENAME}_all.deb"
apt-get install -y /tmp/ros2-apt-source.deb

echo "==> Install ROS 2 Jazzy desktop + dev tools"
apt-get update
# NOTE: deliberately NO 'apt-get upgrade' here. This is a production DGX Spark
# with a source-built Isaac Sim + a running vLLM; a blanket upgrade would bump
# CUDA/nvidia/docker/systemd and risk that stack. Install only ROS + its deps.
# ros-base (NOT desktop): desktop pulls python3-vtk9 which CONFLICTS with the
# system's python3-paraview (OpenFOAM CFD tooling) — so RViz2/VTK is deferred.
# ros-base gives ros2 CLI + rclpy + messages; ros-dev-tools gives colcon. That
# is enough to build the ROS 2 bridge seam and verify camera topics with
# `ros2 topic echo` (RViz2 image display is a later nice-to-have).
apt-get install -y ros-jazzy-ros-base ros-dev-tools

echo
echo "==> Done. ROS 2 Jazzy installed at /opt/ros/jazzy"
echo "    Verify (as the normal user):"
echo "      source /opt/ros/jazzy/setup.bash && ros2 --version && ros2 doctor"
echo "    Add to your shell rc if you want it always sourced:"
echo "      echo 'source /opt/ros/jazzy/setup.bash' >> ~/.bashrc"
