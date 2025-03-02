#!/bin/bash

# Clean previous builds
rm -rf build dist package

# Create necessary directories
mkdir -p package/usr/{bin,share/blackchat}
mkdir -p package/DEBIAN

# Build client using venv's pyinstaller
.venv/bin/pyinstaller --onefile --name blackchat \
--add-data "4chat/assets/notification.png:assets" \
--hidden-import=websockets \
--hidden-import=plyer.platforms.linux.notification \
4chat/client/client.py

# Copy files to package
cp dist/blackchat package/usr/bin/
cp 4chat/assets/notification.png package/usr/share/blackchat/

# Create control file
cat <<EOL > package/DEBIAN/control
Package: blackchat
Version: 1.2
Section: net
Priority: optional
Architecture: amd64
Maintainer: 4INSEC <mwaijegakelvin@gmail.com>
Description: Secure Chat Client for 4INSEC
Homepage: https://4insec.com
Depends: python3, python3-websockets, python3-plyer
EOL

# Build Debian package
dpkg-deb --build package blackchat_1.2_amd64.deb

echo "Package built: blackchat_1.2_amd64.deb"