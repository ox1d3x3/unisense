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
        $res = ['dnsbl' => false, 'stats' => false, 'filter' => false, 'nat' => false, 'errors' => []];
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
            $want = [
                'enabled' => $provider === 'unbound' ? '1' : '0',
                'type' => implode(',', $this->listCodes($unbound)),
                'allowlists' => implode(',', $allow),
                'wildcards' => implode(',', array_map('strtolower', self::csv($this->block))),
                'description' => self::POLICY,
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
        $this->setManaged($ids);
        $this->serializeToConfig(false, true);
        Config::getInstance()->save(['description' => $description]);
        return $res;
    }
}
