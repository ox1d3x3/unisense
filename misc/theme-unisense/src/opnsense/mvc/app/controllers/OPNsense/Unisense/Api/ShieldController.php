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
use OPNsense\Unisense\Shield;

/**
 * UniSense Shield: country blocking, threat lists, blocks and alerts
 * (configd: "unisense shield ...", firewall rules: models/OPNsense/Unisense/Shield.php)
 * @package OPNsense\Unisense
 */
class ShieldController extends ApiControllerBase
{
    const FIELDS = ['enabled', 'countries', 'feeds', 'allowlist', 'vtkey', 'abuseipdbkey'];

    private function runJson($action, $timeout = 60)
    {
        $out = trim((new Backend())->configdRun($action, false, $timeout));
        $data = json_decode($out, true);
        if (!is_array($data)) {
            return ['ok' => false, 'error' => $out === '' ? 'no response from configd' : substr($out, 0, 300)];
        }
        return $data;
    }

    private function settings($mdl = null)
    {
        $mdl = $mdl ?? new Shield();
        $list = function ($v) {
            return array_values(array_filter(explode(',', (string)$v), 'strlen'));
        };
        return [
            'enabled' => (string)$mdl->enabled === '1',
            'countries' => $list($mdl->countries),
            'feeds' => $list($mdl->feeds),
            'allowlist' => $list($mdl->allowlist),
            // keys are never sent back to the browser
            'vtkey' => (string)$mdl->vtkey !== '',
            'abuseipdbkey' => (string)$mdl->abuseipdbkey !== '',
        ];
    }

    /**
     * save settings, update the firewall rules, reload pf, refill the tables
     * @param callable $change function (array $settings): array $settings
     */
    private function change($change, $description)
    {
        $cnf = Config::getInstance();
        $cnf->lock();
        try {
            $mdl = new Shield();
            $before = $this->settings($mdl);
            $new = $change($before);
            $set = [];
            foreach (self::FIELDS as $f) {
                if (!array_key_exists($f, $new)) {
                    continue;
                }
                $v = $new[$f];
                if (in_array($f, ['vtkey', 'abuseipdbkey'])) {
                    if (!is_string($v)) {
                        continue;     // true/false from settings(): unchanged
                    }
                    $set[$f] = trim($v);
                } elseif ($f === 'enabled') {
                    $set[$f] = !empty($v) && $v !== '0' ? '1' : '0';
                } else {
                    $v = is_array($v) ? $v : explode(',', (string)$v);
                    $v = array_values(array_unique(array_filter(array_map('trim', $v), 'strlen')));
                    if ($f === 'countries') {
                        $v = array_map('strtoupper', $v);
                        sort($v);
                    }
                    $set[$f] = implode(',', $v);
                }
            }
            $mdl->setNodes($set);
            $validations = [];
            foreach ($mdl->performValidation() as $msg) {
                $validations['shield.' . $msg->getField()] = $msg->getMessage();
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
        if ($res['changed']) {
            if ($res['aliases']) {
                $backend->configdRun('template reload OPNsense/Filter');
            }
            $backend->configdRun('filter reload');
        }
        $after = $this->settings();
        $apply = $this->runJson('unisense shield apply kill', 300);
        // lists switched on, or a new list chosen: fetch them now (in the background)
        if ($after['enabled'] && (!$before['enabled'] || array_diff($after['feeds'], $before['feeds']))) {
            $backend->configdRun('unisense shield update', true);
        }
        return ['result' => 'saved', 'settings' => $after, 'apply' => $apply];
    }

    /**
     * settings + status (lists, table sizes, counters, unseen alerts)
     */
    public function getAction()
    {
        return ['settings' => $this->settings(), 'status' => $this->runJson('unisense shield status', 60)];
    }

    /**
     * POST {enabled, countries[], feeds[], allowlist[], vtkey, abuseipdbkey} (any subset)
     */
    public function setAction()
    {
        if (!$this->request->isPost()) {
            return ['result' => 'failed', 'error' => 'POST required'];
        }
        $in = $this->request->getPost('shield');
        if (!is_array($in)) {
            $in = json_decode($this->request->getRawBody(), true)['shield'] ?? null;
        }
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
        }, 'UniSense Shield: settings saved');
    }

    private function country($cc)
    {
        $cc = strtoupper((string)$cc);
        return preg_match('/^[A-Z]{2}$/', $cc) ? $cc : null;
    }

    /**
     * block one country (Traffic Map); its open connections are ended
     */
    public function blockAction($cc = '')
    {
        if (!$this->request->isPost() || ($cc = $this->country($cc)) === null) {
            return ['result' => 'failed', 'error' => 'POST a two-letter country code'];
        }
        return $this->change(function ($s) use ($cc) {
            $s['countries'][] = $cc;
            return $s;
        }, sprintf('UniSense Shield: blocked country %s', $cc));
    }

    public function unblockAction($cc = '')
    {
        if (!$this->request->isPost() || ($cc = $this->country($cc)) === null) {
            return ['result' => 'failed', 'error' => 'POST a two-letter country code'];
        }
        return $this->change(function ($s) use ($cc) {
            $s['countries'] = array_values(array_diff($s['countries'], [$cc]));
            return $s;
        }, sprintf('UniSense Shield: unblocked country %s', $cc));
    }

    /** a POST field, sent as a form or as a JSON body */
    private function input($key)
    {
        $v = $this->request->getPost($key);
        if ($v === null) {
            $body = json_decode($this->request->getRawBody(), true);
            $v = is_array($body) ? ($body[$key] ?? null) : null;
        }
        return is_scalar($v) ? (string)$v : '';
    }

    private function address($a)
    {
        $a = trim((string)$a);
        $parts = explode('/', $a, 2);
        if (filter_var($parts[0], FILTER_VALIDATE_IP) === false) {
            return null;
        }
        if (count($parts) == 2 && (!ctype_digit($parts[1]) || (int)$parts[1] > 128)) {
            return null;
        }
        return $a;
    }

    /**
     * POST {address, duration: 1h|6h|24h|1mo|restart|forever}: let a flagged address through
     */
    public function allowAction()
    {
        if (!$this->request->isPost()) {
            return ['ok' => false, 'error' => 'POST required'];
        }
        $addr = $this->address($this->input('address'));
        $dur = $this->input('duration');
        if ($addr === null) {
            return ['ok' => false, 'error' => 'invalid address'];
        }
        if ($dur === 'forever') {
            $r = $this->change(function ($s) use ($addr) {
                $s['allowlist'][] = $addr;
                return $s;
            }, sprintf('UniSense Shield: never block %s', $addr));
            // a timed exception for the same address is no longer needed
            (new Backend())->configdpRun('unisense shield unallow', [$addr]);
            return ['ok' => ($r['result'] ?? '') === 'saved'] + $r;
        }
        if (!in_array($dur, ['1h', '6h', '24h', '1mo', 'restart'])) {
            return ['ok' => false, 'error' => 'unknown duration'];
        }
        $out = json_decode(trim((new Backend())->configdpRun('unisense shield allow', [$addr, $dur], false, 120)), true);
        return is_array($out) ? $out : ['ok' => false, 'error' => 'no response from configd'];
    }

    /**
     * POST {address}: block it again (timed exception or never-block entry)
     */
    public function unallowAction()
    {
        if (!$this->request->isPost()) {
            return ['ok' => false, 'error' => 'POST required'];
        }
        $addr = $this->address($this->input('address'));
        if ($addr === null) {
            return ['ok' => false, 'error' => 'invalid address'];
        }
        if (in_array($addr, $this->settings()['allowlist'])) {
            $this->change(function ($s) use ($addr) {
                $s['allowlist'] = array_values(array_diff($s['allowlist'], [$addr]));
                return $s;
            }, sprintf('UniSense Shield: block %s again', $addr));
        }
        $out = json_decode(trim((new Backend())->configdpRun('unisense shield unallow', [$addr], false, 120)), true);
        return is_array($out) ? $out : ['ok' => false, 'error' => 'no response from configd'];
    }

    public function statusAction()
    {
        return $this->runJson('unisense shield status', 60);
    }

    public function hitsAction()
    {
        return $this->runJson('unisense shield hits', 60);
    }

    public function alertsAction()
    {
        return $this->runJson('unisense shield alerts', 60);
    }

    public function ackAction()
    {
        if (!$this->request->isPost()) {
            return ['ok' => false, 'error' => 'POST required'];
        }
        return $this->runJson('unisense shield ack', 20);
    }

    /**
     * download the lists now (all of them, not only the ones that are due)
     */
    public function updateAction()
    {
        if (!$this->request->isPost()) {
            return ['ok' => false, 'error' => 'POST required'];
        }
        return $this->runJson('unisense shield update force', 300);
    }

    /**
     * what is known about one address: country, network, lists, reputation
     */
    public function checkAction($ip = '')
    {
        if (filter_var((string)$ip, FILTER_VALIDATE_IP) === false) {
            return ['ok' => false, 'error' => 'invalid address'];
        }
        $out = trim((new Backend())->configdpRun('unisense shield check', [(string)$ip], false, 60));
        $data = json_decode($out, true);
        return is_array($data) ? $data : ['ok' => false, 'error' => 'no response from configd'];
    }

    public function checkupAction()
    {
        return $this->runJson('unisense shield checkup', 30);
    }
}
