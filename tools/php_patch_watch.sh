#!/bin/bash
# php_patch_watch.sh - ждёт выход патча PHP (PHP2Shell, CVE-2026-109618/109619), ставит его
# и САМ снимает временную меру с multipart. Всё пишет в лог; apache трогает только после configtest.
LOG=/var/log/php2shell_patch.log
ts() { date -u +%FT%TZ; }
echo "[$(ts)] --- check ---" >> "$LOG"
apt-get update -qq >> "$LOG" 2>&1
cur=$(dpkg-query -W -f='${Version}' php7.4-cli 2>/dev/null)
cand=$(apt-cache policy php7.4-cli 2>/dev/null | awk '/Candidate:/{print $2}')
echo "[$(ts)] installed=$cur candidate=$cand" >> "$LOG"
if [ -z "$cand" ] || [ "$cur" = "$cand" ]; then
  echo "[$(ts)] нового пакета ещё нет, мера остаётся" >> "$LOG"
  exit 0
fi
pkgs=$(dpkg-query -W -f='${binary:Package} ' 'php7.4*' 'libapache2-mod-php7.4' 2>/dev/null)
echo "[$(ts)] ставлю: $pkgs" >> "$LOG"
DEBIAN_FRONTEND=noninteractive apt-get install -y --only-upgrade $pkgs >> "$LOG" 2>&1
if ! apache2ctl configtest >> "$LOG" 2>&1; then
  echo "[$(ts)] ОШИБКА: configtest не прошёл, apache НЕ перезапускаю, мера остаётся" >> "$LOG"
  exit 1
fi
systemctl reload apache2 >> "$LOG" 2>&1
systemctl restart apache2 >> "$LOG" 2>&1
new=$(dpkg-query -W -f='${Version}' php7.4-cli 2>/dev/null)
echo "[$(ts)] PATCHED $cur -> $new apache=$(systemctl is-active apache2)" >> "$LOG"

# снимаем временную меру по multipart - она больше не нужна
if [ -f /etc/apache2/conf-enabled/cody-php2shell-mitigation.conf ]; then
  a2disconf cody-php2shell-mitigation >> "$LOG" 2>&1
  if apache2ctl configtest >> "$LOG" 2>&1; then
    systemctl reload apache2 >> "$LOG" 2>&1
    echo "[$(ts)] временная мера снята (multipart снова разрешён всем)" >> "$LOG"
  else
    a2enconf cody-php2shell-mitigation >> "$LOG" 2>&1
    echo "[$(ts)] ВНИМАНИЕ: без меры configtest не прошёл - мера ВОЗВРАЩЕНА" >> "$LOG"
  fi
fi

for h in teaindigo.com cleantrade.info; do
  for t in GET MULTI; do
    if [ "$t" = GET ]; then
      code=$(curl -sSk -o /dev/null -w '%{http_code}' --resolve "$h:443:127.0.0.1" "https://$h/")
    else
      code=$(curl -sSk -o /dev/null -w '%{http_code}' -F 'x=1' --resolve "$h:443:127.0.0.1" "https://$h/")
    fi
    echo "[$(ts)] проба $h $t -> $code" >> "$LOG"
  done
done
exit 0
