#!/usr/local/bin/php
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


/*
 * UniSense Privacy (see models/OPNsense/Unisense/Privacy.php).
 *
 *   setup_privacy.php sync     bring Unbound's policy and the rules in line with
 *                              the settings; prints the parts that changed
 *   setup_privacy.php sync apply   …and apply it (Shield, when an approved website starts or ends)
 *   setup_privacy.php remove   take everything UniSense made out (uninstall)
 *   setup_privacy.php          what is in place, as JSON
 *
 * The caller applies what changed (see apply_privacy in install.sh).
 */
/* core notices (e.g. 25.7's Unbound model without an OpenVPN section) belong on stderr */
ini_set('display_errors', 'stderr');
require_once('script/load_phalcon.php');

use OPNsense\Core\Config;
use OPNsense\Unisense\Privacy;

$action = $argv[1] ?? '';
$cnf = Config::getInstance();

try {
    if ($action === 'sync' || $action === 'remove') {
        $cnf->lock();
        $mdl = new Privacy();
        if ($action === 'remove' && (string)$mdl->managed === '') {
            $cnf->unlock();
            echo "none" . PHP_EOL;
            exit(0);
        }
        $res = $mdl->syncAndSave($action === 'remove', $action === 'remove' ?
            'UniSense: removed the Privacy policy and rules' : 'UniSense Privacy: settings applied');
        $cnf->unlock();
        if (count($res['errors'])) {
            fwrite(STDERR, 'Privacy settings not valid: ' . implode('; ', $res['errors']) . PHP_EOL);
            exit(1);
        }
        $changed = array_keys(array_filter($res, function ($v, $k) {
            return $k !== 'errors' && $v;
        }, ARRAY_FILTER_USE_BOTH));
        echo (count($changed) ? implode(' ', $changed) : ($action === 'remove' ? 'none' : 'unchanged')) . PHP_EOL;
        if (($argv[2] ?? '') === 'apply' && count($changed)) {
            $backend = new \OPNsense\Core\Backend();
            if ($res['dnsbl'] || $res['stats']) {
                $backend->configdRun('template reload OPNsense/Unbound/*');
                if ($res['stats']) {
                    $backend->configdRun('unbound restart');
                }
                $backend->configdRun('unbound dnsbl');
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
        }
    } else {
        $mdl = new Privacy();
        echo json_encode(['managed' => $mdl->managedIds(), 'provider' => (string)$mdl->provider]) . PHP_EOL;
    }
} catch (Throwable $e) {
    $cnf->unlock();
    fwrite(STDERR, 'could not update the Privacy settings: ' . $e->getMessage() . PHP_EOL);
    exit(1);
}
