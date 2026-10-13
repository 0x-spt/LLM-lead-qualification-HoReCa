#!/bin/zsh
# Installs the auto-collection services into launchd (~/Library/LaunchAgents/io.leadgen.*.plist).
#   deploy/local/install.sh                 # database, VPN exits (xray), bot, registry, grader, backup
#   deploy/local/install.sh workers         # plus Instagram workers (after the proxies)
#   deploy/local/install.sh --remove        # remove everything
set -e
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
AGENTS=$HOME/Library/LaunchAgents
RUN=$ROOT/deploy/local/run.sh
mkdir -p "$AGENTS" "$ROOT/logs"

plist() {  # name, module arguments, mode (keepalive|once|daily)
  local name=$1 args=$2 mode=$3 file=$AGENTS/io.leadgen.$1.plist
  local progargs="<string>$RUN</string>"
  for a in ${=args}; do progargs="$progargs<string>$a</string>"; done
  local sched="<key>KeepAlive</key><true/><key>ThrottleInterval</key><integer>30</integer>"
  [ "$mode" = once ] && sched="<key>KeepAlive</key><false/>"
  [ "$mode" = daily ] && sched="<key>StartCalendarInterval</key><dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>30</integer></dict>"
  cat > "$file" <<P
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>io.leadgen.$name</string>
<key>ProgramArguments</key><array>$progargs</array>
<key>RunAtLoad</key><true/>
$sched
<key>StandardOutPath</key><string>$ROOT/logs/$name.log</string>
<key>StandardErrorPath</key><string>$ROOT/logs/$name.log</string>
</dict></plist>
P
  launchctl bootout gui/$(id -u)/io.leadgen.$name 2>/dev/null || true
  # bootout is asynchronous: wait for the service to disappear, otherwise bootstrap errors with 5
  for i in 1 2 3 4 5 6 7 8 9 10; do launchctl print gui/$(id -u)/io.leadgen.$name >/dev/null 2>&1 || break; sleep 1; done
  launchctl bootstrap gui/$(id -u) "$file"
  echo "  $name"
}

if [ "$1" = "--remove" ]; then
  for f in $AGENTS/io.leadgen.*.plist(N); do launchctl bootout gui/$(id -u) "$f" 2>/dev/null || true; rm -f "$f"; echo "  снят $(basename $f)"; done
  exit 0
fi
echo "ставлю сервисы:"
plist postgres "leadgen.core.localpg" once
sleep 3
plist xray "leadgen.access.vpn run" keepalive
plist bot "leadgen.ops.bot" keepalive
plist replica "leadgen.core.replica" keepalive
plist registry "leadgen.sources.registry" keepalive
plist websearch "leadgen.sources.websearch" keepalive
plist grader "leadgen.llm.grader loop" keepalive
plist backup "leadgen.core.backup" daily
plist awards "leadgen.sources.awards" daily
[ "$1" = "workers" ] && plist workers "leadgen.ops.workers" keepalive
echo "логи: $ROOT/logs/"
