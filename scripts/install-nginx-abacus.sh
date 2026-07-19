#!/usr/bin/env sh
set -eu
timestamp="$(date +%Y%m%d-%H%M%S)"
sudo cp /etc/nginx/nginx.conf "/etc/nginx/nginx.conf.backup-${timestamp}"
sudo cp deploy/nginx.conf /etc/nginx/nginx.conf
sudo nginx -t
sudo systemctl reload nginx
echo "Nginx configurado. Backup: /etc/nginx/nginx.conf.backup-${timestamp}"
