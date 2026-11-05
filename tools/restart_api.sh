#!/bin/sh
# Плановый перезапуск CleanTrade Payment API.
#
# Зачем обёртка, а не «systemctl restart» руками: сторож видит остановку
# сервиса как «детект мёртв» и будит оператора. Здесь на время работ ставится
# метка data/maintenance.until (самоистекает через 30 минут), а после старта
# снимается - забыть её нельзя.
set -e
START=$(date +%s)
echo $((START + 1800)) > /root/CleanProd/data/maintenance.until
systemctl restart stablepay
for i in 1 2 3 4 5 6 7 8 9 10; do
    sleep 1
    if systemctl is-active --quiet stablepay; then
        break
    fi
done
systemctl is-active stablepay
# Пульс пишется в первые же секунды жизни сервиса; ждём его, чтобы уйти
# из окна работ уже с живым доказательством, а не с пустым файлом.
for i in 1 2 3 4 5 6 7 8 9 10; do
    if [ -s /root/CleanProd/data/poller_heartbeat.json ]; then
        NEW=$(stat -c %Y /root/CleanProd/data/poller_heartbeat.json)
        if [ "$NEW" -ge "$START" ]; then
            break
        fi
    fi
    sleep 1
done
rm -f /root/CleanProd/data/maintenance.until
echo "pulse: $(cat /root/CleanProd/data/poller_heartbeat.json)"
