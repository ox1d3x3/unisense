<?php

/*
 * Copyright (C) 2026 ox1d3x3
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice,
 *    this list of conditions and the following disclaimer.
 *
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
 * INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
 * AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 * AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
 * OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 * SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 * INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 * CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 * ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 * POSSIBILITY OF SUCH DAMAGE.
 */

namespace OPNsense\Unisense;

use OPNsense\Base\BaseModel;
use OPNsense\Core\Config;
use OPNsense\Firewall\Filter;
use OPNsense\Unbound\Unbound;

/**
 * UniSense Privacy: ad and tracker blocking.
 *
 * Built in, it manages one policy in Unbound's own blocklist
 * (Services: Unbound DNS: Blocklist, described "UniSense Privacy") and
 * turns on Unbound's query statistics; with AdGuard Home it only talks to
 * that server (scripts/unisense/privacy.py). Either way it can add:
 *   - a firewall rule refusing DNS-over-TLS (port 853) to anything but the
 *     chosen DNS server, on the chosen interfaces;
 *   - built in, on 26.1 and later: port forwards sending every plain DNS query
 *     (port 53) on those interfaces to the firewall, so devices that hard-code
 *     8.8.8.8 still go through the blocklist.
 * Everything it creates is recorded in "managed" and removed on uninstall.
 * @package OPNsense\Unisense
 */
class Privacy extends BaseModel
{
    const POLICY = 'UniSense Privacy';

    /* hagezi / OISD lists shipped with Unbound's blocklist (same codes 25.7 - 26.7) */
    const LEVELS = [
        'light' => ['hgz001', 'hgz013'],               // LIGHT + threat intelligence (mini)
        'balanced' => ['hgz003', 'hgz011'],            // PRO + threat intelligence
        'strict' => ['hgz005', 'hgz011', 'hgz017'],    // PRO++ + threat intelligence + badware hosters
    ];
    const EXTRAS = [
        'scams' => ['hgz009'],
        'popups' => ['hgz010'],
        'gambling' => ['hgz019'],
        'social' => ['hgz022'],                        // 26.1 and later
        'adult' => ['oisd2'],
        'bypass' => ['hgz014'],                        // DoH / VPN / Tor / proxy services
    ];

    /* never blocked when "gaming-safe" is on: logins, stores, matchmaking, anti-cheat, voice */
    const GAMING = [
        'xboxlive.com', 'xbox.com', 'playstation.com', 'playstation.net', 'sonyentertainmentnetwork.com',
        'steampowered.com', 'steamcommunity.com', 'steamserver.net', 'steamcontent.com', 'steamstatic.com',
        'battle.net', 'blizzard.com', 'ubi.com', 'ubisoft.com', 'ubisoftconnect.com',
        'activision.com', 'callofduty.com', 'demonware.net', 'ea.com', 'epicgames.com', 'unrealengine.com',
        'riotgames.com', 'battleye.com', 'easyanticheat.net', 'nintendo.net', 'nintendo.com',
        'discord.com', 'discord.gg', 'discord.media', 'discordapp.com', 'discordapp.net',
    ];

    public function managedIds()
    {
        $res = [];
        foreach (explode(',', (string)$this->managed) as $item) {
            $p = explode(':', $item, 2);
            if (count($p) == 2) {
                $res[$p[0]] = $p[1];
            }
        }
        return $res;
    }

    private function setManaged($ids)
    {
        $items = [];
        foreach ($ids as $k => $v) {
            $items[] = $k . ':' . $v;
        }
        $this->managed = implode(',', $items);
    }

    /**
     * websites approved for a while from Shield's unblock requests (Shield keeps and ends them)
     * @return string[]
     */
    public static function approvedDomains()
    {
        $dir = getenv('UNISENSE_SHIELD_DIR') ?: '/var/db/unisense/shield';
        $st = @json_decode((string)@file_get_contents($dir . '/state.json'), true);
        $res = [];
        foreach (is_array($st) && is_array($st['domain_allows'] ?? null) ? $st['domain_allows'] : [] as $d => $a) {
            $until = (int)($a['until'] ?? 0);
            if (($until === 0 || $until > time()) && preg_match('/^[a-z0-9_.-]{1,253}$/', (string)$d)) {
                $res[] = (string)$d;
            }
        }
        sort($res);
        return $res;
    }

    /** an allow-list pattern for a domain and its subdomains (Unbound's allowlist takes regular expressions) */
    public static function pattern($domain)
    {
        return '(^|\.)' . preg_quote(strtolower(trim($domain, '.')), null) . '$';
    }

    private static function csv($v)
    {
        return array_values(array_filter(array_map('trim', explode(',', (string)$v)), 'strlen'));
    }

    /**
     * the blocklist codes for the current settings, only those this core knows
     */
    public function listCodes($unbound)
    {
        $known = array_keys($unbound->dnsbl->blocklist->getTemplateNode()->type->getNodeData());
        $codes = self::LEVELS[(string)$this->level] ?? self::LEVELS['balanced'];
        foreach (self::csv($this->extras) as $x) {
            $codes = array_merge($codes, self::EXTRAS[$x] ?? []);
        }
        return array_values(array_intersect(array_unique($codes), $known));
    }

    /** which extras this core offers */
    public function availableExtras($unbound)
    {
        $known = array_keys($unbound->dnsbl->blocklist->getTemplateNode()->type->getNodeData());
        $res = [];
        foreach (self::EXTRAS as $x => $codes) {
            if (!array_diff($codes, $known)) {
                $res[] = $x;
            }
        }
        return $res;
    }

    public function interfaceList()
    {
        $ifs = Config::getInstance()->object()->interfaces;
        return array_values(array_filter(self::csv($this->interfaces), function ($i) use ($ifs) {
            return isset($ifs->$i) && $i !== 'wan';
        }));
    }

    private $vip = null;

    private function vipModel()
    {
        if ($this->vip === null) {
            $this->vip = new \OPNsense\Interfaces\Vip();
        }
        return $this->vip;
    }

    /**
     * the block page: an address on the loopback, rules that let devices reach it,
     * unblock.<domain>, and (26.1+) web visits to known-bad addresses sent to it
     */
    private function syncBlockPage($remove, &$ids, &$res, $unbound, $filter, &$nat)
    {
        $on = $this->blockPageWanted($remove);
        $addr = (string)$this->bp_address;
        $ifs = $on ? $this->interfaceList() : [];

        /* the address, on lo0 */
        $vip = $this->vipModel();
        $node = isset($ids['bp-vip']) ? $vip->getNodeByReference('vip.' . $ids['bp-vip']) : null;
        if ($node !== null && (!$on || (string)$node->subnet !== $addr)) {
            /* like the core's own Virtual IPs page: "interface vip configure" removes the old address */
            @file_put_contents('/tmp/delete_vip_' . $ids['bp-vip'] . '.todo', (string)$node->subnet . PHP_EOL, FILE_APPEND);
        }
        if (!$on && $node !== null) {
            $vip->vip->del($ids['bp-vip']);
            unset($ids['bp-vip']);
            $res['vip'] = true;
        } elseif ($on) {
            $want = ['mode' => 'ipalias', 'interface' => 'lo0', 'subnet' => $addr, 'subnet_bits' => '32',
                'descr' => 'UniSense block page'];
            if ($node === null) {
                $node = $vip->vip->Add();
                $ids['bp-vip'] = $node->getAttribute('uuid');
            }
            foreach ($want as $k => $v) {
                if ((string)$node->$k !== $v) {
                    $node->$k = $v;
                    $res['vip'] = true;
                }
            }
        }
        if ($res['vip']) {
            foreach ($vip->performValidation() as $msg) {
                $res['errors'][] = 'vip ' . $msg->getField() . ': ' . $msg->getMessage();
            }
        }

        /* devices may reach it on 80 and 443 */
        foreach (['bp-http' => '80', 'bp-https' => '443'] as $key => $port) {
            $rule = isset($ids[$key]) ? $filter->getNodeByReference('rules.rule.' . $ids[$key]) : null;
            if ((!$on || !count($ifs)) && $rule !== null) {
                $filter->rules->rule->del($ids[$key]);
                unset($ids[$key]);
                $res['filter'] = true;
            } elseif ($on && count($ifs)) {
                $want = ['enabled' => '1', 'action' => 'pass', 'quick' => '1', 'direction' => 'in',
                    'interface' => implode(',', $ifs), 'protocol' => 'TCP', 'ipprotocol' => 'inet',
                    'source_net' => 'any', 'destination_net' => $addr, 'destination_port' => $port, 'log' => '0',
                    'description' => 'UniSense: block page'];
                if ($rule === null) {
                    $rule = $filter->rules->rule->Add();
                    $ids[$key] = $rule->getAttribute('uuid');
                    $rule->sequence = '5';
                }
                foreach ($want as $k => $v) {
                    if ((string)$rule->$k !== $v) {
                        $rule->$k = $v;
                        $res['filter'] = true;
                    }
                }
            }
        }
        if ($res['filter']) {
            foreach ($filter->performValidation() as $msg) {
                $msg = 'filter ' . $msg->getField() . ': ' . $msg->getMessage();
                if (!in_array($msg, $res['errors'])) {
                    $res['errors'][] = $msg;
                }
            }
        }

        /* unblock.<domain> */
        $host = isset($ids['bp-host']) ? $unbound->getNodeByReference('hosts.host.' . $ids['bp-host']) : null;
        if (!$on && $host !== null) {
            $unbound->hosts->host->del($ids['bp-host']);
            unset($ids['bp-host']);
            $res['dnsbl'] = true;
        } elseif ($on) {
            $want = ['enabled' => '1', 'hostname' => 'unblock', 'domain' => self::systemDomain(), 'rr' => 'A',
                'server' => $addr, 'description' => 'UniSense: unblock requests'];
            if ($host === null) {
                $host = $unbound->hosts->host->Add();
                $ids['bp-host'] = $host->getAttribute('uuid');
            }
            foreach ($want as $k => $v) {
                if ((string)$host->$k !== $v) {
                    $host->$k = $v;
                    $res['dnsbl'] = true;
                }
            }
        }

        /* web visits to known-bad addresses go to the page (26.1+, when Shield's lists are on) */
        if ($nat === null && class_exists('\OPNsense\Firewall\DNat')) {
            $nat = new \OPNsense\Firewall\DNat();
        }
        if ($nat !== null) {
            $alias = false;
            foreach ((new \OPNsense\Firewall\Alias())->aliases->alias->iterateItems() as $a) {
                $alias = $alias || (string)$a->name === 'unisense_threats';
            }
            $ports = (string)$this->bp_ca !== '' ? ['80', '443'] : ['80'];
            $want = [];
            if ($on && $alias) {
                foreach ($ifs as $if) {
                    foreach ($ports as $p) {
                        $want['bp-thr-' . $if . '-' . $p] = [$if, $p];
                    }
                }
            }
            foreach ($ids as $k => $uuid) {
                if (strpos($k, 'bp-thr-') === 0 && !isset($want[$k])) {
                    $nat->rule->del($uuid);
                    unset($ids[$k]);
                    $res['nat'] = true;
                }
            }
            foreach ($want as $k => [$if, $p]) {
                $fields = ['interface' => $if, 'ipprotocol' => 'inet', 'protocol' => 'TCP',
                    'source' => ['network' => 'any'], 'destination' => ['network' => 'unisense_threats', 'port' => $p],
                    'target' => $addr, 'local-port' => $p, 'pass' => 'pass',
                    'descr' => 'UniSense Shield: known-bad sites on ' . $if . ' show the block page'];
                $n = isset($ids[$k]) ? $nat->getNodeByReference('rule.' . $ids[$k]) : null;
                if ($n === null) {
                    $n = $nat->rule->Add();
                    $ids[$k] = $n->getAttribute('uuid');
                    $n->setNodes($fields);
                    $res['nat'] = true;
                } elseif ((string)$n->target !== $addr) {
                    $n->target = $addr;
                    $res['nat'] = true;
                }
            }
            if ($res['nat']) {
                foreach ($nat->performValidation() as $msg) {
                    $msg = 'nat ' . $msg->getField() . ': ' . $msg->getMessage();
                    if (!in_array($msg, $res['errors'])) {
                        $res['errors'][] = $msg;
                    }
                }
            }
        }
    }

    /** the block page is on: built in only (it needs Unbound to answer with its address) */
    public function blockPageWanted($remove = false)
    {
        return !$remove && (string)$this->provider === 'unbound' && (string)$this->bp_enabled === '1' &&
            (string)$this->bp_address !== '';
    }

    /** the system domain, for unblock.<domain> */
    public static function systemDomain()
    {
        $d = (string)(Config::getInstance()->object()->system->domain ?? '');
        return $d !== '' ? $d : 'home.arpa';
    }

    /** the CAs in System: Trust: Authorities that have a private key (to sign the block page's certificates) */
    public static function authorities()
    {
        $res = [];
        foreach (Config::getInstance()->object()->ca ?? [] as $ca) {
            if (!empty((string)$ca->prv)) {
                $res[] = ['refid' => (string)$ca->refid, 'descr' => (string)$ca->descr];
            }
        }
        return $res;
    }

    /**
     * the block page's own settings file and the CA it signs with (it runs as www)
     * @return bool changed
     */
    public function writeBlockPageConf($remove = false)
    {
        $dir = getenv('UNISENSE_BLOCKPAGE_DIR') ?: '/var/db/unisense/blockpage';
        $on = $this->blockPageWanted($remove);
        $crt = $key = '';
        if ($on && (string)$this->bp_ca !== '') {
            foreach (Config::getInstance()->object()->ca ?? [] as $ca) {
                if ((string)$ca->refid === (string)$this->bp_ca && !empty((string)$ca->prv)) {
                    $crt = base64_decode((string)$ca->crt);
                    $key = base64_decode((string)$ca->prv);
                }
            }
        }
        $conf = json_encode(['enabled' => $on, 'address' => (string)$this->bp_address,
            'domain' => self::systemDomain(), 'ca' => $key !== '', 'ca_refid' => $key !== '' ? (string)$this->bp_ca : '']);
        $old = @file_get_contents($dir . '/conf.json');
        $oldCrt = @file_get_contents($dir . '/ca.crt');
        if (($old === $conf && (string)$oldCrt === $crt) || (!$on && $old === false)) {
            return false;            // unchanged, or off and never set up
        }
        @mkdir($dir, 0755, true);
        @mkdir($dir . '/inbox', 0750, true);
        @mkdir($dir . '/certs', 0755, true);
        $files = ['conf.json' => [$conf, 0644], 'ca.crt' => [$crt, 0644], 'ca.key' => [$key, 0640]];
        foreach ($files as $name => [$data, $mode]) {
            if ($data === '' && $name !== 'conf.json') {
                @unlink($dir . '/' . $name);
                continue;
            }
            @unlink($dir . '/' . $name . '.new');
            file_put_contents($dir . '/' . $name . '.new', $data);
            chmod($dir . '/' . $name . '.new', $mode);
            if ($name === 'ca.key' && function_exists('posix_getgrnam') && ($gr = posix_getgrnam('www'))) {
                @chgrp($dir . '/' . $name . '.new', $gr['gid']);       // the block page signs with it as www
            }
            rename($dir . '/' . $name . '.new', $dir . '/' . $name);
        }
        /* a new CA: certificates signed by the old one must go */
        if ((string)$oldCrt !== $crt) {
            foreach (glob($dir . '/certs/*.pem') ?: [] as $f) {
                @unlink($f);
            }
        }
        /* the block page (www) may write only these two; everything else stays root's */
        if (function_exists('posix_getpwnam') && ($pw = posix_getpwnam('www')) && posix_geteuid() === 0) {
            foreach (['/inbox', '/certs'] as $f) {
                if (file_exists($dir . $f)) {
                    @chown($dir . $f, $pw['uid']);
                    @chgrp($dir . $f, $pw['gid']);
                }
            }
        }
        return true;
    }

    /** the DNS server clients should use: the firewall itself, or the AdGuard Home host */
    public function dnsServer()
    {
        if ((string)$this->provider === 'adguard') {
            $host = parse_url((string)$this->agh_url, PHP_URL_HOST);
            $host = trim((string)$host, '[]');
            return filter_var($host, FILTER_VALIDATE_IP) ? $host : null;
        }
        return '(self)';
    }

    /**
     * Bring Unbound's policy, the firewall rule and the port forwards in line
     * with the settings, and save (one revision; caller holds the config lock).
     * @param bool $remove take everything out (uninstall)
     * @return array changed: dnsbl, stats, filter, nat; errors
     */
    public function syncAndSave($remove = false, $description = 'UniSense Privacy: settings saved')
    {
        $res = ['dnsbl' => false, 'stats' => false, 'filter' => false, 'nat' => false, 'vip' => false,
            'blockpage' => false, 'errors' => []];
        $ids = $this->managedIds();
        $provider = $remove ? 'off' : (string)$this->provider;

        /* ---- Unbound blocklist policy (built in) */
        $unbound = new Unbound();
        $node = isset($ids['policy']) ? $unbound->getNodeByReference('dnsbl.blocklist.' . $ids['policy']) : null;
        if ($remove && $node !== null) {
            $unbound->dnsbl->blocklist->del($ids['policy']);
            unset($ids['policy']);
            $res['dnsbl'] = true;
        } elseif (!$remove && ($provider === 'unbound' || $node !== null)) {
            $allow = [];
            if ((string)$this->gaming === '1') {
                foreach (self::GAMING as $d) {
                    $allow[] = self::pattern($d);
                }
            }
            foreach (self::csv($this->allow) as $d) {
                $allow[] = self::pattern($d);
            }
            foreach (self::approvedDomains() as $d) {
                $allow[] = self::pattern($d);
            }
            $want = [
                'enabled' => $provider === 'unbound' ? '1' : '0',
                'type' => implode(',', $this->listCodes($unbound)),
                'allowlists' => implode(',', $allow),
                'wildcards' => implode(',', array_map('strtolower', self::csv($this->block))),
                'description' => self::POLICY,
                /* blocked names point at the block page (empty: Unbound's 0.0.0.0) */
                'address' => $this->blockPageWanted($remove) ? (string)$this->bp_address : '',
            ];
            if ($node === null) {
                $node = $unbound->dnsbl->blocklist->Add();
                $ids['policy'] = $node->getAttribute('uuid');
                $node->setNodes($want);
                $res['dnsbl'] = true;
            } else {
                foreach ($want as $k => $v) {
                    if ((string)$node->$k !== $v) {
                        $node->$k = $v;
                        $res['dnsbl'] = true;
                    }
                }
            }
        }
        if ($provider === 'unbound' && (string)$unbound->general->stats !== '1') {
            /* the dashboard reads Unbound's own query statistics */
            $unbound->general->stats = '1';
            $res['stats'] = true;
        }
        if ($res['dnsbl'] || $res['stats']) {
            foreach ($unbound->performValidation() as $msg) {
                $res['errors'][] = 'unbound ' . $msg->getField() . ': ' . $msg->getMessage();
            }
        }

        /* ---- DNS-over-TLS: refuse port 853 except to the chosen server */
        $filter = new Filter();
        $server = $this->dnsServer();
        $wantDot = !$remove && $provider !== 'off' && (string)$this->dot === '1' && $server !== null &&
            count($this->interfaceList());
        $rule = isset($ids['dot']) ? $filter->getNodeByReference('rules.rule.' . $ids['dot']) : null;
        if (!$wantDot && $rule !== null) {
            $filter->rules->rule->del($ids['dot']);
            unset($ids['dot']);
            $res['filter'] = true;
        } elseif ($wantDot) {
            $want = [
                'enabled' => '1', 'action' => 'reject', 'quick' => '1', 'direction' => 'in',
                'interface' => implode(',', $this->interfaceList()), 'protocol' => 'TCP/UDP',
                /* an address must match the rule's family; "this firewall" covers both */
                'ipprotocol' => $server === '(self)' ? 'inet46' : (strpos($server, ':') !== false ? 'inet6' : 'inet'),
                'source_net' => 'any', 'destination_net' => $server, 'destination_not' => '1',
                'destination_port' => '853', 'log' => '0',
                'description' => 'UniSense Privacy: refuse DNS-over-TLS to other servers',
            ];
            if ($rule === null) {
                $rule = $filter->rules->rule->Add();
                $ids['dot'] = $rule->getAttribute('uuid');
                $rule->setNodes(array_merge($want, ['sequence' => '10']));
                $res['filter'] = true;
            } else {
                foreach ($want as $k => $v) {
                    if ((string)$rule->$k !== $v) {
                        $rule->$k = $v;
                        $res['filter'] = true;
                    }
                }
            }
        }
        if ($res['filter']) {
            foreach ($filter->performValidation() as $msg) {
                $res['errors'][] = 'filter ' . $msg->getField() . ': ' . $msg->getMessage();
            }
        }

        /* ---- plain DNS: send it to the firewall (built in, 26.1 and later) */
        $nat = null;
        if (class_exists('\OPNsense\Firewall\DNat')) {
            $nat = new \OPNsense\Firewall\DNat();
            $wantIfs = (!$remove && $provider === 'unbound' && (string)$this->redirect === '1') ? $this->interfaceList() : [];
            foreach ($ids as $k => $uuid) {
                if (strpos($k, 'rdr-') === 0 && !in_array(substr($k, 4), $wantIfs)) {
                    $nat->rule->del($uuid);
                    unset($ids[$k]);
                    $res['nat'] = true;
                }
            }
            foreach ($wantIfs as $if) {
                $k = 'rdr-' . $if;
                $n = isset($ids[$k]) ? $nat->getNodeByReference('rule.' . $ids[$k]) : null;
                if ($n === null) {
                    $n = $nat->rule->Add();
                    $ids[$k] = $n->getAttribute('uuid');
                    $n->setNodes([
                        'interface' => $if, 'ipprotocol' => 'inet', 'protocol' => 'TCP/UDP',
                        'source' => ['network' => 'any'],
                        'destination' => ['network' => $if . 'ip', 'not' => '1', 'port' => '53'],
                        'target' => '127.0.0.1', 'local-port' => '53', 'pass' => 'pass',
                        'descr' => 'UniSense Privacy: DNS on ' . $if . ' goes to this firewall',
                    ]);
                    $res['nat'] = true;
                }
            }
            if ($res['nat']) {
                foreach ($nat->performValidation() as $msg) {
                    $res['errors'][] = 'nat ' . $msg->getField() . ': ' . $msg->getMessage();
                }
            }
        }

        $this->syncBlockPage($remove, $ids, $res, $unbound, $filter, $nat);

        if (count($res['errors'])) {
            return $res;
        }
        if ($res['dnsbl'] || $res['stats']) {
            $unbound->serializeToConfig(false, true);
        }
        if ($res['filter']) {
            $filter->serializeToConfig(false, true);
        }
        if ($res['nat']) {
            $nat->serializeToConfig(false, true);
        }
        if ($res['vip']) {
            $this->vipModel()->serializeToConfig(false, true);
        }
        $this->setManaged($ids);
        $this->serializeToConfig(false, true);
        Config::getInstance()->save(['description' => $description]);
        $res['blockpage'] = $this->writeBlockPageConf($remove) || $res['vip'];
        return $res;
    }
}
