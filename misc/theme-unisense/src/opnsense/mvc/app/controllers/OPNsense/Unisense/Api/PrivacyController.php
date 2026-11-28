<?php

/*
 * Copyright (C) 2026 Ox1d3x3
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

namespace OPNsense\Unisense\Api;

use OPNsense\Base\ApiControllerBase;
use OPNsense\Core\Backend;
use OPNsense\Core\Config;
use OPNsense\Unbound\Unbound;
use OPNsense\Unisense\Privacy;

/**
 * UniSense Privacy: ad and tracker blocking, built in (Unbound) or AdGuard Home
 * (models/OPNsense/Unisense/Privacy.php, scripts/unisense/privacy.py)
 * @package OPNsense\Unisense
 */
class PrivacyController extends ApiControllerBase
{
    const FIELDS = ['provider', 'level', 'extras', 'gaming', 'allow', 'block', 'agh_url', 'agh_user', 'agh_pass',
        'redirect', 'dot', 'interfaces', 'bp_enabled', 'bp_address', 'bp_ca'];

    private function runJson($action, $params = [], $timeout = 60)
    {
        $b = new Backend();
        $out = trim(count($params) ? $b->configdpRun($action, $params, false, $timeout) : $b->configdRun($action, false, $timeout));
        $data = json_decode($out, true);
        return is_array($data) ? $data : ['ok' => false, 'error' => $out === '' ? 'no response from configd' : substr($out, 0, 300)];
    }

    /** a POST field, sent as a form or as a JSON body */
    private function input($key)
    {
        $v = $this->request->getPost($key);
        if ($v === null) {
            $body = json_decode($this->request->getRawBody(), true);
            $v = is_array($body) ? ($body[$key] ?? null) : null;
        }
        return $v;
    }

    private static function csv($v)
    {
        return array_values(array_filter(array_map('trim', explode(',', (string)$v)), 'strlen'));
    }

    private function settings($mdl = null)
    {
        $mdl = $mdl ?? new Privacy();
        return [
            'provider' => (string)$mdl->provider, 'level' => (string)$mdl->level, 'extras' => self::csv($mdl->extras),
            'gaming' => (string)$mdl->gaming === '1', 'allow' => self::csv($mdl->allow), 'block' => self::csv($mdl->block),
            'agh_url' => (string)$mdl->agh_url, 'agh_user' => (string)$mdl->agh_user,
            'agh_pass' => (string)$mdl->agh_pass !== '',        // never sent back
            'redirect' => (string)$mdl->redirect === '1', 'dot' => (string)$mdl->dot === '1',
            'interfaces' => self::csv($mdl->interfaces),
            'bp_enabled' => (string)$mdl->bp_enabled === '1', 'bp_address' => (string)$mdl->bp_address,
            'bp_ca' => (string)$mdl->bp_ca,
        ];
    }

    /**
     * settings + what this firewall offers (extras on this core, interfaces, NAT model)
     */
    public function getAction()
    {
        $mdl = new Privacy();
        $unbound = new Unbound();
        $ifs = [];
        foreach (Config::getInstance()->object()->interfaces->children() as $name => $node) {
            if ($name !== 'wan' && empty($node->virtual) && !empty($node->if)) {
                $ifs[] = ['name' => $name, 'descr' => !empty($node->descr) ? (string)$node->descr : strtoupper($name)];
            }
        }
        return [
            'settings' => $this->settings($mdl),
            'info' => [
                'extras' => $mdl->availableExtras($unbound), 'gaming' => Privacy::GAMING, 'interfaces' => $ifs,
                'redirect' => class_exists('\OPNsense\Firewall\DNat'),
                'unbound' => (string)$unbound->general->enabled === '1',
                'policies' => count(iterator_to_array($unbound->dnsbl->blocklist->iterateItems())),
                'cas' => Privacy::authorities(), 'domain' => Privacy::systemDomain(),
            ],
        ];
    }

    /**
     * save, update Unbound / the rules, apply what changed
     * @param callable $change function (array $settings): array $settings
     */
    private function change($change, $description)
    {
        $cnf = Config::getInstance();
        $cnf->lock();
        try {
            $mdl = new Privacy();
            $new = $change($this->settings($mdl));
            $set = [];
            foreach (self::FIELDS as $f) {
                if (!array_key_exists($f, $new)) {
                    continue;
                }
                $v = $new[$f];
                if ($f === 'agh_pass') {
                    if (is_string($v)) {
                        $set[$f] = $v;          // true/false from settings(): unchanged
                    }
                } elseif (in_array($f, ['gaming', 'redirect', 'dot', 'bp_enabled'])) {
                    $set[$f] = !empty($v) && $v !== '0' && $v !== 'false' ? '1' : '0';
                } elseif (is_array($v)) {
                    $v = array_values(array_unique(array_filter(array_map(function ($x) {
                        return strtolower(trim((string)$x, " \t."));
                    }, $v), 'strlen')));
                    $set[$f] = implode(',', $v);
                } else {
                    $set[$f] = trim((string)$v);
                }
            }
            $mdl->setNodes($set);
            $validations = [];
            foreach ($mdl->performValidation() as $msg) {
                $validations['privacy.' . $msg->getField()] = $msg->getMessage();
            }
            if (count($validations)) {
                $cnf->unlock();
                return ['result' => 'failed', 'validations' => $validations];
            }
            $res = $mdl->syncAndSave(false, $description);
            $cnf->unlock();
        } catch (\Throwable $e) {
            $cnf->unlock();
            return ['result' => 'failed', 'error' => $e->getMessage()];
        }
        if (count($res['errors'])) {
            return ['result' => 'failed', 'error' => implode('; ', $res['errors'])];
        }
        $backend = new Backend();
        if ($res['dnsbl'] || $res['stats']) {
            $backend->configdRun('template reload OPNsense/Unbound/*');
            if ($res['stats']) {
                $backend->configdRun('unbound restart');
            }
            // downloads the lists the first time: let it run in the background
            $backend->configdRun('unbound dnsbl', true);
        }
        if ($res['filter'] || $res['nat']) {
            $backend->configdRun('filter reload');
        }
        if ($res['vip']) {
            $backend->configdRun('interface vip configure');
        }
        if ($res['blockpage']) {
            $backend->configdRun('unisense blockpage restart');
        }
        return ['result' => 'saved', 'settings' => $this->settings(), 'applied' => array_keys(array_filter($res, function ($v, $k) {
            return $k !== 'errors' && $v;
        }, ARRAY_FILTER_USE_BOTH))];
    }

    /**
     * POST {privacy: {...}} (any subset of FIELDS)
     */
    public function setAction()
    {
        if (!$this->request->isPost()) {
            return ['result' => 'failed', 'error' => 'POST required'];
        }
        $in = $this->input('privacy');
        if (!is_array($in)) {
            return ['result' => 'failed', 'error' => 'nothing to save'];
        }
        return $this->change(function ($s) use ($in) {
            foreach (self::FIELDS as $f) {
                if (array_key_exists($f, $in)) {
                    $s[$f] = $in[$f];
                }
            }
            return $s;
        }, 'UniSense Privacy: settings saved');
    }

    /**
     * POST {domain, action: allow|block|remove}: from the dashboard and the query log
     */
    public function domainAction()
    {
        if (!$this->request->isPost()) {
            return ['ok' => false, 'error' => 'POST required'];
        }
        $domain = strtolower(trim((string)$this->input('domain'), " \t."));
        $action = (string)$this->input('action');
        if (!preg_match('/^(?=.{1,253}$)([a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?\.)+[a-z0-9-]{2,63}$/', $domain) ||
                !in_array($action, ['allow', 'block', 'remove'])) {
            return ['ok' => false, 'error' => 'invalid domain or action'];
        }
        if ((string)(new Privacy())->provider === 'adguard') {
            return $this->runJson('unisense privacy rule', [$action, $domain], 30);
        }
        $r = $this->change(function ($s) use ($domain, $action) {
            $s['allow'] = array_values(array_diff($s['allow'], [$domain]));
            $s['block'] = array_values(array_diff($s['block'], [$domain]));
            if ($action === 'allow') {
                $s['allow'][] = $domain;
            } elseif ($action === 'block') {
                $s['block'][] = $domain;
            }
            return $s;
        }, sprintf('UniSense Privacy: %s %s', $action, $domain));
        return ['ok' => ($r['result'] ?? '') === 'saved'] + $r;
    }

    /** is the block page running, on which address */
    public function blockpageAction()
    {
        return $this->runJson('unisense blockpage status', [], 15);
    }

    public function summaryAction()
    {
        return $this->runJson('unisense privacy summary');
    }

    public function logAction($n = '200')
    {
        return $this->runJson('unisense privacy log', [(string)max(10, min(1000, (int)$n))]);
    }

    public function testAction()
    {
        return $this->runJson('unisense privacy test', [], 20);
    }

    /** AdGuard Home: POST {on: 1|0} */
    public function protectionAction()
    {
        if (!$this->request->isPost()) {
            return ['ok' => false, 'error' => 'POST required'];
        }
        $on = $this->input('on');
        return $this->runJson('unisense privacy protection', [!empty($on) && $on !== '0' ? 'on' : 'off'], 20);
    }

    /** AdGuard Home: add the gaming-safe allow rules */
    public function gamingAction()
    {
        if (!$this->request->isPost()) {
            return ['ok' => false, 'error' => 'POST required'];
        }
        return $this->runJson('unisense privacy gaming', [], 30);
    }
}
