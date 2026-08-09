#!/usr/bin/env bash
# 把 server_setup.sh 以脱离 SSH 会话的方式启动，避免连接断开导致安装中断。
cd /root/autodl-tmp || exit 1
sed -i 's/\r$//' server_setup.sh
chmod +x server_setup.sh
: > setup_stdout.log
setsid nohup bash server_setup.sh >setup_stdout.log 2>&1 </dev/null &
sleep 3
echo "LAUNCHED pid=$!"
tail -5 setup_stdout.log
