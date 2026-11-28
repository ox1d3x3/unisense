#!/bin/sh
# Run the installer's PHP parts for real — settheme.php, run_migrations.php and
# the Network Map model — against an OPNsense core checkout, with PHP 8 on Linux.
#
#   tests/test-real-php.sh CORE_DIR
#
# CORE_DIR is a checkout of opnsense/core with at least src/opnsense/mvc and
# src/etc. OPNsense code uses absolute paths, so this needs root: it links
# /usr/local/opnsense and creates /conf for the duration of the test and
# refuses to run if either already exists for real.
# shellcheck disable=SC2016,SC2034  # assertions are evaluated later, on purpose
set -eu
cd "$(dirname "$0")/.."
CORE="$(cd "${1:?usage: $0 CORE_DIR}" && pwd)"
[ -f "$CORE/src/opnsense/mvc/script/load_phalcon.php" ] || { echo "$CORE is not an OPNsense core checkout"; exit 2; }
[ "$(id -u)" -eq 0 ] || { echo "needs root (uses /usr/local/opnsense and /conf)"; exit 2; }
for p in /usr/local/opnsense /conf; do
	if [ -e "$p" ] && [ ! -L "$p" ] && [ ! -f "$p/.unisense-test" ]; then
		echo "$p exists — refusing to touch a real system"; exit 2
	fi
done
command -v php >/dev/null || { echo "php is required"; exit 2; }
php -m | grep -qi '^phalcon$' || { echo "the phalcon PHP extension is required (OPNsense's MVC uses it)"; exit 2; }

PASS=0
FAIL=0
# t NAME ASSERTION [show]: with "show", print $out when the assertion fails
t() {
	if eval "$2"; then PASS=$((PASS + 1)); echo "  ok   $1"; return 0; fi
	FAIL=$((FAIL + 1)); echo "  FAIL $1"
	[ "${3:-}" != show ] || printf '%s\n' "$out" | sed 's/^/       | /'
}

S="$(mktemp -d)"
MADE=""
cleanup() {
	[ -n "${CONFIGD_PID:-}" ] && kill "$CONFIGD_PID" 2>/dev/null
	rm -f /var/run/configd.socket
	rm -f /usr/local/opnsense
	rm -rf /conf "$S"
	rm -f /var/lib/php/tmp/configdmodelfield.data
	for d in $MADE; do rm -rf "$d"; done
}
trap cleanup EXIT

# a minimal /usr/local/opnsense: the real mvc tree plus an empty web root
mkdir -p "$S/opnsense/www/themes/opnsense/build/css"
cp -R "$CORE/src/opnsense/mvc" "$S/opnsense/mvc"
echo '<?php' > "$S/opnsense/www/index.php"
mkdir -p /usr/local
ln -sfn "$S/opnsense" /usr/local/opnsense
mkdir -p /conf/backup
touch /conf/.unisense-test
for d in /var/lib/php/tmp /var/lib/php/cache; do
	[ -d "$d" ] || { mkdir -p "$d"; MADE="$MADE $d"; }
done
cp "$CORE/src/etc/config.xml.sample" /conf/config.xml
grep -q '<theme>' /conf/config.xml || sed -i 's:</opnsense>:  <theme>opnsense</theme>\n</opnsense>:' /conf/config.xml
cp /conf/config.xml "$S/config.orig.xml"

# php with OPNsense's include path (see /usr/local/etc/php.ini on a firewall)
cat > "$S/php" <<EOF
#!/bin/sh
exec php -d include_path=".:/usr/local/opnsense/mvc:$CORE/src/etc/inc:/usr/local/opnsense/contrib" -d error_reporting=E_ALL -d display_errors=stderr "\$@"
EOF
chmod +x "$S/php"
PHP="$S/php"
# configd stand-ins: record what the installer asks for
printf '#!/bin/sh\necho "service $*" >> "%s/calls"\n' "$S" > "$S/service"
printf '#!/bin/sh\necho "configctl $*" >> "%s/calls"\n' "$S" > "$S/configctl"
chmod +x "$S/service" "$S/configctl"
export UNISENSE_SERVICE="$S/service" UNISENSE_CONFIGCTL="$S/configctl"
# the cron model validates commands against configd's action list; core
# caches that list here until configd restarts (no socket here: never stale)
printf '%s' '{"unisense geoip update": {"description": "UniSense: update IP-to-country databases (Traffic Map)"}, "unisense trafficmap sample": {"description": "UniSense: record Traffic Map history (every minute)"}, "unisense shield update": {"description": "UniSense: refresh the Shield threat lists (every 15 minutes)"}, "firmware poll": {"description": "Firmware update check"}}' \
    > /var/lib/php/tmp/configdmodelfield.data
SETUPCRON=/usr/local/opnsense/scripts/unisense/setup_cron.php
SETTHEME=/usr/local/opnsense/scripts/unisense/settheme.php
theme() { sed -n 's:.*<theme>\([^<]*\)</theme>.*:\1:p' /conf/config.xml; }
# the config minus the parts a save is expected to touch
stable() { sed -e '/<theme>/d' -e '/<revision>/,/<\/revision>/d' -e '/<OPNsense>/,/<\/OPNsense>/d' "$1"; }

echo "install (real php)"
out="$(UNISENSE_PREFIX=/ UNISENSE_PHP="$PHP" sh install.sh --local . --yes 2>&1)" || true
t "installer finishes without warnings" '! printf "%s" "$out" | grep -q -E "^ ! |Fatal|Warning|Deprecated"' show
t "theme set to unisense" '[ "$(theme)" = unisense ]'
t "revision written" 'grep -q "<description>GUI theme set to unisense</description>" /conf/config.xml'
t "backup made" '[ -n "$(ls /conf/backup)" ]'
t "nothing else in the config changed" 'stable "$S/config.orig.xml" | diff -q - "$(stable /conf/config.xml > "$S/now.xml"; echo "$S/now.xml")" >/dev/null'
t "network map config section created" 'grep -q "<unisense>" /conf/config.xml'

echo "Traffic Map cron jobs (real cron model)"
t "configd restarted for the new actions" 'grep -q "service configd restart" "$S/calls"'
t "cron job saved" 'grep -q "<command>unisense geoip update</command>" /conf/config.xml && grep -q "<origin>UniSense</origin>" /conf/config.xml'
t "IP database at 01:00" '"$PHP" "$SETUPCRON" 2>&1 | grep -qx "0 1 \* \* \* unisense geoip update"' show
t "Shield lists refreshed every 15 minutes" '"$PHP" "$SETUPCRON" 2>&1 | grep -qx "\*/15 \* \* \* \* unisense shield update"' show
t "history sampled every minute" '"$PHP" "$SETUPCRON" 2>&1 | grep -qx "\* \* \* \* \* unisense trafficmap sample"' show
t "crontab regenerated" 'grep -q "configctl cron restart" "$S/calls"'
t "first download started in the background" 'grep -q "configctl -dq unisense geoip update" "$S/calls"'
t "second install keeps the existing jobs" '[ "$("$PHP" "$SETUPCRON" install 2>&1)" = exists ] && [ "$(grep -c "<command>unisense geoip update</command>" /conf/config.xml)" = 1 ] && [ "$(grep -c "<command>unisense trafficmap sample</command>" /conf/config.xml)" = 1 ] && [ "$(grep -c "<command>unisense shield update</command>" /conf/config.xml)" = 1 ]'

echo "settheme.php"
t "prints the current theme" '[ "$("$PHP" "$SETTHEME" 2>&1)" = unisense ]'
t "setting the same theme is a no-op" '[ "$("$PHP" "$SETTHEME" unisense 2>&1)" = unchanged ]'
t "unknown theme is refused" '! "$PHP" "$SETTHEME" nope >/dev/null 2>&1 && [ "$(theme)" = unisense ]'
t "bad theme name is refused" '! "$PHP" "$SETTHEME" "../x" >/dev/null 2>&1'
sed -i '/<theme>/d' /conf/config.xml
t "missing <theme> reads as opnsense" '[ "$("$PHP" "$SETTHEME" 2>&1)" = opnsense ]'
t "missing <theme> can be set" '[ "$("$PHP" "$SETTHEME" unisense 2>&1)" = ok ] && [ "$(theme)" = unisense ]'

echo "network map model (real BaseModel)"
cat > "$S/model.php" <<'PHP'
<?php
require_once('script/load_phalcon.php');
$m = new \OPNsense\Unisense\NetworkMap();
$check = function ($mac, $name, $icon) use ($m) {
    $d = $m->devices->device->Add();
    $d->setNodes(['mac' => $mac, 'name' => $name, 'icon' => $icon]);
    $n = count($m->performValidation());
    $m->devices->device->del($d->getAttributes()['uuid']);
    return $n === 0 ? 'valid' : 'invalid';
};
echo $check('aa:bb:cc:dd:ee:01', 'Living room TV', 'tv'), "\n";
echo $check('aa:bb:cc:dd:ee:02', '<script>', 'tv'), "\n";
echo $check('aa:bb:cc:dd:ee:03', 'NAS', 'rocket'), "\n";
echo $check('not-a-mac', 'x', 'auto'), "\n";
$d = $m->devices->device->Add();
$d->setNodes(['mac' => 'aa:bb:cc:dd:ee:04', 'name' => 'Kitchen speaker', 'icon' => 'speaker']);
$m->serializeToConfig();
\OPNsense\Core\Config::getInstance()->save();
echo "saved\n";
PHP
out="$("$PHP" "$S/model.php" 2>&1)" || true
t "model loads and validates a good device" '[ "$(printf "%s\n" "$out" | sed -n 1p)" = valid ]' show
t "HTML in a name is rejected" '[ "$(printf "%s\n" "$out" | sed -n 2p)" = invalid ]'
t "unknown icon is rejected" '[ "$(printf "%s\n" "$out" | sed -n 3p)" = invalid ]'
t "bad MAC is rejected" '[ "$(printf "%s\n" "$out" | sed -n 4p)" = invalid ]'
t "device saved to config" 'grep -q "Kitchen speaker" /conf/config.xml'

echo "Shield (real Alias / Filter models)"
# some firewall model fields list their choices through configd: a stand-in
# that answers every query with an empty JSON object
[ -e /var/run/configd.socket ] && { echo "/var/run/configd.socket exists — refusing"; exit 2; }
python3 - "$S/configd.log" <<'PY' &
import os, socket, sys
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.bind('/var/run/configd.socket')
s.listen(8)
while True:
    c, _ = s.accept()
    cmd = c.recv(4096).decode()
    open(sys.argv[1], 'a').write(cmd + '\n')
    c.sendall(b'{}' + b'\0\0\0')
    c.close()
PY
CONFIGD_PID=$!
sleep 1
SETUPSHIELD=/usr/local/opnsense/scripts/unisense/setup_shield.php
cat > "$S/shield.php" <<'PHP'
<?php
require_once('script/load_phalcon.php');
use OPNsense\Core\Config;
$m = new \OPNsense\Unisense\Shield();
$cmd = $argv[1];
if ($cmd === 'valid') {
    $m->setNodes([$argv[2] => $argv[3]]);
    $errs = [];
    foreach ($m->performValidation() as $e) {
        $errs[] = $e->getField();
    }
    echo count($errs) ? 'invalid' : 'valid', "\n";
    if (count($errs) && getenv('SHOW')) {
        foreach ($m->performValidation() as $e) {
            fwrite(STDERR, $e->getField() . ': ' . $e->getMessage() . "\n");
        }
    }
    exit(0);
}
if ($cmd === 'set') {
    $set = [];
    for ($i = 2; $i + 1 < count($argv); $i += 2) {
        $set[$argv[$i]] = $argv[$i + 1];
    }
    $m->setNodes($set);
    if (count($m->performValidation())) {
        echo "invalid\n";
        exit(1);
    }
    $m->serializeToConfig();
    Config::getInstance()->save();
    echo "saved\n";
    exit(0);
}
if ($cmd === 'pf') {
    // render the Shield rules the way the firewall does
    $filter = new \OPNsense\Firewall\Filter();
    $ifs = [];
    $gws = [];
    foreach ($m->ruleIds() as $kind => $uuid) {
        $node = $filter->getNodeByReference('rules.rule.' . $uuid);
        $r = new \OPNsense\Firewall\FilterRule($ifs, $gws, $node->serialize());
        echo $kind, ' ', trim(preg_replace('/\s+/', ' ', (string)$r)), "\n";
    }
    exit(0);
}
if ($cmd === 'delrule') {
    $filter = new \OPNsense\Firewall\Filter();
    $filter->rules->rule->del($m->ruleIds()[$argv[2]]);
    $filter->serializeToConfig(false, true);
    Config::getInstance()->save();
    echo "deleted\n";
}
PHP
sh_() { "$PHP" "$S/shield.php" "$@" 2>&1; }
t "no Shield rules before it is used" '"$PHP" "$SETUPSHIELD" 2>&1 | grep -q "\"rules\":\[\]"' show
t "country codes validated" '[ "$(sh_ valid countries CN,RU)" = valid ] && [ "$(sh_ valid countries china)" = invalid ] && [ "$(sh_ valid countries cn)" = invalid ]'
t "threat lists validated" '[ "$(sh_ valid feeds spamhaus,feodo,tor)" = valid ] && [ "$(sh_ valid feeds spamhaus,evil)" = invalid ]'
out="$(SHOW=1 sh_ valid allowlist 1.2.3.4,10.0.0.0/8,2001:db8::/32) $(sh_ valid allowlist nope) $(sh_ valid allowlist any)"
t "allow list validated" '[ "$out" = "valid invalid invalid" ]' show
t "API keys validated" '[ "$(sh_ valid vtkey abcDEF0123)" = valid ] && [ "$(sh_ valid vtkey "a b;rm")" = invalid ]'
out="$(sh_ set countries CN,RU enabled 1)"
t "settings saved" '[ "$out" = saved ]' show
out="$("$PHP" "$SETUPSHIELD" sync 2>&1)" || true
t "aliases and rules created" '[ "$out" = changed ]' show
st="$("$PHP" "$SETUPSHIELD" 2>&1)"
t "three External aliases" 'printf "%s" "$st" | grep -q "\"unisense_geoblock\":\"external\",\"unisense_threats\":\"external\",\"unisense_scanners\":\"external\""' show
t "five rules, all on" '[ "$(printf "%s" "$st" | grep -o "\"enabled\":true" | wc -l)" -eq 5 ]'
out="$(sh_ pf)"
t "pf: drop countries from the Internet" 'printf "%s\n" "$out" | grep -E "^country-in block in log quick inet(46)? from \\\$unisense_geoblock to \\{any\\} label" >/dev/null' show
t "pf: refuse your devices reaching them" 'printf "%s\n" "$out" | grep -E "^country-out block return in log quick inet(46)? from \\{any\\} to \\\$unisense_geoblock label" >/dev/null' show
t "pf: threat lists both ways, scanners inbound only" 'printf "%s\n" "$out" | grep -qE "^threat-in block in .*from \\\$unisense_threats to \\{any\\}" && printf "%s\n" "$out" | grep -qE "^threat-out block return in .*to \\\$unisense_threats" && printf "%s\n" "$out" | grep -qE "^scanner-in block in .*from \\\$unisense_scanners to \\{any\\}" && ! printf "%s\n" "$out" | grep -q "to \\\$unisense_scanners"' show
t "pf: every Shield rule labelled with its id (for the block counters)" '[ "$(printf "%s\n" "$out" | grep -c "label \"[0-9a-f-]\{36\}\"")" -eq 5 ]' show
t "second sync changes nothing" '[ "$("$PHP" "$SETUPSHIELD" sync 2>&1)" = unchanged ]'
sh_ set enabled 0 >/dev/null
out="$("$PHP" "$SETUPSHIELD" sync 2>&1)"
st="$("$PHP" "$SETUPSHIELD" 2>&1)"
t "lists off: their rules off, country rules stay on" '[ "$out" = changed ] && [ "$(printf "%s" "$st" | grep -o "\"enabled\":true" | wc -l)" -eq 2 ]' show
sh_ delrule country-out >/dev/null
t "a deleted rule is put back" '[ "$("$PHP" "$SETUPSHIELD" sync 2>&1)" = changed ] && [ "$("$PHP" "$SETUPSHIELD" 2>&1 | grep -o "\"uuid\"" | wc -l)" -eq 5 ]'
sh_ set countries "" >/dev/null
"$PHP" "$SETUPSHIELD" sync >/dev/null 2>&1
t "no countries: country rules off" '[ "$("$PHP" "$SETUPSHIELD" 2>&1 | grep -o "\"enabled\":true" | wc -l)" -eq 0 ]'
sh_ set countries CN >/dev/null
"$PHP" "$SETUPSHIELD" sync >/dev/null 2>&1

echo "Shield API controller (real models, configd stand-in)"
cat > "$S/api.php" <<'PHP'
<?php
require_once('script/load_phalcon.php');
// the controller's save path without an HTTP request
$c = new \OPNsense\Unisense\Api\ShieldController();
$change = new ReflectionMethod($c, 'change');
$change->setAccessible(true);
$run = function ($fn) use ($c, $change) {
    $r = $change->invoke($c, $fn, 'test');
    return ($r['result'] ?? '') . (isset($r['validations']) ? ':' . implode(',', array_keys($r['validations'])) : '') .
        (isset($r['settings']) ? ' ' . implode(',', $r['settings']['countries']) . ' ' . implode(',', $r['settings']['feeds']) .
        ' ' . ($r['settings']['enabled'] ? 'on' : 'off') : '');
};
echo $run(function ($s) { $s['countries'][] = 'ru'; return $s; }), "\n";
echo $run(function ($s) { $s['countries'][] = 'Atlantis'; return $s; }), "\n";
echo $run(function ($s) { $s['countries'] = array_diff($s['countries'], ['CN']); return $s; }), "\n";
echo $run(function ($s) { $s['feeds'] = ['spamhaus', 'tor']; $s['enabled'] = '1'; return $s; }), "\n";
echo $run(function ($s) { $s['vtkey'] = 'abc123'; return $s; }), "\n";
echo $run(function ($s) { $s['feeds'] = []; return $s; }), "\n";
PHP
: > "$S/configd.log"
out="$("$PHP" "$S/api.php" 2>&1)" || true
t "block: code upper-cased and saved, rules updated" '[ "$(printf "%s\n" "$out" | sed -n 1p)" = "saved CN,RU spamhaus,feodo,et,cins,dshield off" ]' show
t "invalid country refused, nothing saved" '[ "$(printf "%s\n" "$out" | sed -n 2p)" = "failed:shield.countries" ] && [ "$(printf "%s\n" "$out" | sed -n 3p)" = "saved RU spamhaus,feodo,et,cins,dshield off" ]' show
t "unblock" '[ "$(printf "%s\n" "$out" | sed -n 3p)" = "saved RU spamhaus,feodo,et,cins,dshield off" ]' show
t "lists switched on with a choice of lists" '[ "$(printf "%s\n" "$out" | sed -n 4p)" = "saved RU spamhaus,tor on" ]' show
t "API key saved, never sent back" '[ "$(printf "%s\n" "$out" | sed -n 5p)" = "saved RU spamhaus,tor on" ] && grep -q "<vtkey>abc123</vtkey>" /conf/config.xml' show
t "every list off is saved as empty" '[ "$(printf "%s\n" "$out" | sed -n 6p)" = "saved RU  on" ] && grep -qE "<feeds */>|<feeds></feeds>" /conf/config.xml' show
t "firewall reloaded and tables loaded through configd" 'grep -q "^filter reload" "$S/configd.log" && grep -q "^unisense shield apply kill" "$S/configd.log"' show
t "list download started when the lists were switched on" 'grep -q "unisense shield update" "$S/configd.log"' show

echo "Privacy (real Unbound / Filter / NAT models)"
SETUPPRIV=/usr/local/opnsense/scripts/unisense/setup_privacy.php
cat > "$S/priv.php" <<'PHP'
<?php
ini_set('display_errors', 'stderr');
require_once('script/load_phalcon.php');
use OPNsense\Core\Config;
$m = new \OPNsense\Unisense\Privacy();
$cmd = $argv[1];
if ($cmd === 'set') {
    $set = [];
    for ($i = 2; $i + 1 < count($argv); $i += 2) {
        $set[$argv[$i]] = $argv[$i + 1];
    }
    $m->setNodes($set);
    $errs = [];
    foreach ($m->performValidation() as $e) {
        $errs[] = $e->getField();
    }
    if (count($errs)) {
        echo 'invalid:', implode(',', $errs), "\n";
        exit(0);
    }
    $m->serializeToConfig();
    Config::getInstance()->save();
    echo "saved\n";
} elseif ($cmd === 'policy') {
    $u = new \OPNsense\Unbound\Unbound();
    $id = $m->managedIds()['policy'] ?? '';
    $n = $id ? $u->getNodeByReference('dnsbl.blocklist.' . $id) : null;
    echo json_encode($n === null ? null : [
        'enabled' => (string)$n->enabled, 'type' => (string)$n->type, 'allow' => (string)$n->allowlists,
        'wildcards' => (string)$n->wildcards, 'descr' => (string)$n->description, 'stats' => (string)$u->general->stats,
    ]), "\n";
} elseif ($cmd === 'pf') {
    $filter = new \OPNsense\Firewall\Filter();
    $id = $m->managedIds()['dot'] ?? '';
    $n = $id ? $filter->getNodeByReference('rules.rule.' . $id) : null;
    if ($n === null) {
        echo "none\n";
        exit(0);
    }
    $ifs = ['lan' => ['if' => 'em1', 'descr' => 'LAN']];
    $gws = [];
    echo trim(preg_replace('/\s+/', ' ', (string)(new \OPNsense\Firewall\FilterRule($ifs, $gws, $n->serialize())))), "\n";
} elseif ($cmd === 'nat') {
    if (!class_exists('\OPNsense\Firewall\DNat')) {
        echo "n/a\n";
        exit(0);
    }
    $nat = new \OPNsense\Firewall\DNat();
    $out = [];
    foreach ($m->managedIds() as $k => $id) {
        if (strpos($k, 'rdr-') === 0) {
            $n = $nat->getNodeByReference('rule.' . $id);
            $out[] = $n === null ? "$k:missing" : "$k:" . (string)$n->interface . ' ' . (string)$n->protocol . ' !' .
                (string)$n->destination->network . ':' . (string)$n->destination->port . ' -> ' . (string)$n->target . ':' .
                (string)$n->{'local-port'};
        }
    }
    echo count($out) ? implode(';', $out) : 'none', "\n";
} elseif ($cmd === 'bp') {
    $ids = $m->managedIds();
    $o = [];
    $vip = new \OPNsense\Interfaces\Vip();
    $n = isset($ids['bp-vip']) ? $vip->getNodeByReference('vip.' . $ids['bp-vip']) : null;
    $o[] = 'vip=' . ($n === null ? 'none' : (string)$n->interface . ':' . (string)$n->subnet . '/' . (string)$n->subnet_bits);
    $o[] = 'vips=' . count(iterator_to_array($vip->vip->iterateItems()));
    $f = new \OPNsense\Firewall\Filter();
    foreach (['bp-http', 'bp-https'] as $k) {
        $r = isset($ids[$k]) ? $f->getNodeByReference('rules.rule.' . $ids[$k]) : null;
        $o[] = $k . '=' . ($r === null ? 'none' : (string)$r->action . ':' . (string)$r->destination_net . ':' . (string)$r->destination_port);
    }
    $u = new \OPNsense\Unbound\Unbound();
    $h = isset($ids['bp-host']) ? $u->getNodeByReference('hosts.host.' . $ids['bp-host']) : null;
    $o[] = 'host=' . ($h === null ? 'none' : (string)$h->hostname . '.' . (string)$h->domain . '>' . (string)$h->server);
    $p = isset($ids['policy']) ? $u->getNodeByReference('dnsbl.blocklist.' . $ids['policy']) : null;
    $o[] = 'address=' . ($p === null ? '' : (string)$p->address);
    $thr = array_filter(array_keys($ids), function ($k) { return strpos($k, 'bp-thr-') === 0; });
    $o[] = 'thr=' . (class_exists('\OPNsense\Firewall\DNat') ? implode('+', $thr) : 'n/a');
    echo implode(' ', $o), "\n";
}
PHP
pv() { "$PHP" "$S/priv.php" "$@" 2>>"$S/priv.err"; }
out="$("$PHP" "$SETUPPRIV" sync 2>>"$S/priv.err")"
t "nothing made before Privacy is used" '[ "$out" = unchanged ] && [ "$(pv policy)" = null ]' show
t "settings validated" '[ "$(pv set level extreme)" = invalid:level ] && [ "$(pv set extras gambling,evil)" = invalid:extras ] && [ "$(pv set agh_url "javascript:x")" = invalid:agh_url ] && [ "$(pv set allow "a b")" = invalid:allow ]' show
out="$(pv set provider unbound level balanced extras gambling,bypass,social allow gigabyte.com block tiktok.com dot 1 redirect 1 interfaces lan)"
t "settings saved" '[ "$out" = saved ]' show
out="$("$PHP" "$SETUPPRIV" sync 2>>"$S/priv.err")"
t "built in: policy, statistics, firewall rule (and port forward where supported) created" 'printf "%s" "$out" | grep -q "dnsbl" && printf "%s" "$out" | grep -q "stats" && printf "%s" "$out" | grep -q "filter"' show
pol="$(pv policy)"
t "policy: hagezi PRO + threat intel + extras this core has" 'printf "%s" "$pol" | grep -q "\"type\":\"hgz003,hgz011,hgz019,hgz014" && printf "%s" "$pol" | grep -q "\"enabled\":\"1\""' show
t "policy: gaming-safe and your allows as patterns; your blocks as wildcards" 'printf "%s" "$pol" | grep -q "xboxlive" && printf "%s" "$pol" | grep -qF "(^|\\\\.)gigabyte\\\\.com$" && printf "%s" "$pol" | grep -q "\"wildcards\":\"tiktok.com\""' show
t "Unbound statistics switched on for the dashboard" 'printf "%s" "$pol" | grep -q "\"stats\":\"1\""'
pf="$(pv pf)"
t "pf: DNS-over-TLS to anything but the firewall refused on LAN" 'printf "%s" "$pf" | grep -E "block return in quick on em1 inet proto (tcp|\{ ?tcp udp ?\}|\{ ?tcp,? ?udp ?\})" >/dev/null && printf "%s" "$pf" | grep -qE "to ! ?\(self\)|to \{ ?! ?\(self\)" && printf "%s" "$pf" | grep -q "port 853\|port = 853\|port {853}"' show
nat="$(pv nat)"
out="$nat"
t "plain DNS on LAN goes to the firewall (26.1 and later)" '[ "$nat" = n/a ] || [ "$nat" = "rdr-lan:lan tcp/udp !lanip:53 -> 127.0.0.1:53" ]' show
t "second sync changes nothing" '[ "$("$PHP" "$SETUPPRIV" sync 2>>"$S/priv.err")" = unchanged ]'
pv set provider off >/dev/null
out="$("$PHP" "$SETUPPRIV" sync 2>>"$S/priv.err")"
t "off: policy kept but disabled, rules removed" 'printf "%s" "$(pv policy)" | grep -q "\"enabled\":\"0\"" && [ "$(pv pf)" = none ] && { [ "$(pv nat)" = n/a ] || [ "$(pv nat)" = none ]; }' show
out="$(pv set provider adguard agh_url http://192.168.1.5:3000) / $("$PHP" "$SETUPPRIV" sync 2>>"$S/priv.err")" || true
t "AdGuard Home settings applied" 'printf "%s" "$out" | grep -q "^saved / filter"' show
pf="$(pv pf)"
t "AdGuard Home: DNS-over-TLS allowed only to that server" 'printf "%s" "$pf" | grep -q "192.168.1.5" && { [ "$(pv nat)" = n/a ] || [ "$(pv nat)" = none ]; }' show
pv set provider unbound >/dev/null
"$PHP" "$SETUPPRIV" sync >/dev/null 2>&1 || true

echo "Block page (real Vip / Filter / Unbound / NAT models)"
# a real firewall has the loopback interface and, here, a CA with its key
grep -q "<lo0>" /conf/config.xml || sed -i 's:</interfaces>:<lo0><internal_dynamic>1</internal_dynamic><descr>Loopback</descr><enable>1</enable><if>lo0</if><ipaddr>127.0.0.1</ipaddr><subnet>8</subnet><type>none</type><virtual>1</virtual></lo0></interfaces>:' /conf/config.xml
openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj /CN=x1wall -keyout "$S/ca.key" -out "$S/ca.crt" >/dev/null 2>&1
sed -i "s:</opnsense>:<ca><refid>6a1b2c3d4e5f6</refid><descr>x1wall CA</descr><crt>$(base64 -w0 < "$S/ca.crt")</crt><prv>$(base64 -w0 < "$S/ca.key")</prv></ca></opnsense>:" /conf/config.xml
export UNISENSE_BLOCKPAGE_DIR="$S/bp"
t "block page address validated" '[ "$(pv set bp_address 10.0.0.0/24)" = invalid:bp_address ] && [ "$(pv set bp_ca "x;y")" = invalid:bp_ca ]' show
out="$(pv set bp_enabled 1 bp_ca 6a1b2c3d4e5f6) / $("$PHP" "$SETUPPRIV" sync 2>>"$S/priv.err")" || true
t "block page on: applied" 'printf "%s" "$out" | grep -q "^saved / .*vip"' show
out="$(pv bp)"
t "address on the loopback, rules to it on 80 and 443, unblock.<domain>, Unbound answers with it" 'printf "%s" "$out" | grep -q "vip=lo0:198.18.0.80/32" && printf "%s" "$out" | grep -q "bp-http=pass:198.18.0.80:80" && printf "%s" "$out" | grep -q "bp-https=pass:198.18.0.80:443" && printf "%s" "$out" | grep -qE "host=unblock\.[a-z.]+>198.18.0.80" && printf "%s" "$out" | grep -q "address=198.18.0.80"' show
t "known-bad sites sent to the page on 26.1+, not offered on 25.7" 'printf "%s" "$out" | grep -qE "thr=(n/a|bp-thr-lan-80\+bp-thr-lan-443)( |$)"' show
t "its settings file and the CA for www" 'grep -q "\"enabled\":true" "$S/bp/conf.json" && grep -q "\"ca\":true" "$S/bp/conf.json" && cmp -s "$S/bp/ca.crt" "$S/ca.crt" && [ "$(stat -c %a "$S/bp/ca.key")" = 600 ]' show
t "second sync changes nothing" '[ "$("$PHP" "$SETUPPRIV" sync 2>>"$S/priv.err")" = unchanged ]'
pv set bp_address 198.18.0.81 >/dev/null
"$PHP" "$SETUPPRIV" sync >/dev/null 2>>"$S/priv.err" || true
out="$(pv bp)"
t "address change: still one address, moved" 'printf "%s" "$out" | grep -q "vip=lo0:198.18.0.81/32 vips=1" && printf "%s" "$out" | grep -q "address=198.18.0.81" && printf "%s" "$out" | grep -q ">198.18.0.81"' show
pv set bp_enabled 0 >/dev/null
"$PHP" "$SETUPPRIV" sync >/dev/null 2>>"$S/priv.err" || true
out="$(pv bp)"
t "block page off: address, rules, unblock name and redirects gone; Unbound back to 0.0.0.0" 'printf "%s" "$out" | grep -q "vip=none vips=0 bp-http=none bp-https=none host=none address= thr=\(n/a\|\)$" && grep -q "\"enabled\":false" "$S/bp/conf.json"' show
pv set bp_enabled 1 >/dev/null
"$PHP" "$SETUPPRIV" sync >/dev/null 2>>"$S/priv.err" || true

echo "Privacy API controller (real models, configd stand-in)"
cat > "$S/papi.php" <<'PHP'
<?php
ini_set('display_errors', 'stderr');
require_once('script/load_phalcon.php');
$c = new \OPNsense\Unisense\Api\PrivacyController();
$change = new ReflectionMethod($c, 'change');
$change->setAccessible(true);
$get = $c->getAction();
echo json_encode(['extras' => $get['info']['extras'], 'ifs' => array_column($get['info']['interfaces'], 'name'),
    'redirect' => $get['info']['redirect']]), "\n";
$r = $change->invoke($c, function ($s) { $s['allow'][] = 'Example.COM.'; $s['level'] = 'strict'; return $s; }, 't');
echo $r['result'], ' ', implode(',', $r['settings']['allow']), ' ', $r['settings']['level'], ' ', implode('+', $r['applied']), "\n";
$r = $change->invoke($c, function ($s) { $s['level'] = 'insane'; return $s; }, 't');
echo $r['result'], ' ', implode(',', array_keys($r['validations'] ?? [])), "\n";
$r = $change->invoke($c, function ($s) { $s['agh_pass'] = 'pw1'; return $s; }, 't');
echo $r['result'], ' ', json_encode($r['settings']['agh_pass']), "\n";
PHP
: > "$S/configd.log"
out="$("$PHP" "$S/papi.php" 2>>"$S/priv.err")" || true
t "what this core offers: extras, LAN interfaces, port forwards from 26.1" 'printf "%s\n" "$out" | sed -n 1p | grep -q "\"ifs\":\[\"lan\"\]" && printf "%s\n" "$out" | sed -n 1p | grep -q "\"bypass\""' show
t "save: domain cleaned, level changed, Unbound policy updated" 'printf "%s\n" "$out" | sed -n 2p | grep -q "^saved gigabyte.com,example.com strict dnsbl"' show
t "invalid level refused" '[ "$(printf "%s\n" "$out" | sed -n 3p)" = "failed privacy.level" ]' show
t "password saved, never sent back" '[ "$(printf "%s\n" "$out" | sed -n 4p)" = "saved true" ] && grep -q "<agh_pass>pw1</agh_pass>" /conf/config.xml' show
out="$(cat "$S/configd.log")"
t "Unbound templates and blocklist reloaded through configd" 'grep -q "template reload OPNsense/Unbound/\*" "$S/configd.log" && grep -q "unbound dnsbl" "$S/configd.log"' show

echo "uninstall (real php)"
out="$(UNISENSE_PREFIX=/ UNISENSE_PHP="$PHP" sh install.sh --uninstall --yes 2>&1)" || true
t "uninstall finishes without warnings" '! printf "%s" "$out" | grep -q -E "^ ! |Fatal|Warning|Deprecated"' show
t "theme switched back to opnsense" '[ "$(theme)" = opnsense ]'
t "device labels kept in config" 'grep -q "Kitchen speaker" /conf/config.xml'
t "cron jobs removed" '! grep -q "unisense geoip update" /conf/config.xml && ! grep -q "unisense trafficmap sample" /conf/config.xml && ! grep -q "unisense shield update" /conf/config.xml'
t "Privacy policy and rules removed" '! grep -q "UniSense Privacy" /conf/config.xml || ! grep -q "<description>UniSense Privacy</description>" /conf/config.xml'
t "block page address, rules and unblock name removed" '! grep -q "UniSense block page" /conf/config.xml && ! grep -q "UniSense: block page" /conf/config.xml && ! grep -q "UniSense: unblock requests" /conf/config.xml'
t "Shield rules and aliases removed" '! grep -q "<name>unisense_" /conf/config.xml && ! grep -q "UniSense Shield: blocked countries (" /conf/config.xml && ! grep -q "unisense_geoblock" /conf/config.xml'
t "firewall reloaded after removing them" 'grep -q "configctl filter reload" "$S/calls"' show

echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
