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
 * UniSense Shield firewall rules (see models/OPNsense/Unisense/Shield.php).
 *
 *   setup_shield.php sync     create/update the aliases and rules for the
 *                             current settings (prints "changed" or "unchanged")
 *   setup_shield.php remove   take the Shield rules and aliases out (uninstall);
 *                             prints "changed" or "none"
 *   setup_shield.php          the rules and aliases in place, as JSON
 *
 * The caller reloads the firewall when something changed:
 *   configctl template reload OPNsense/Filter && configctl filter reload
 */
require_once('script/load_phalcon.php');

use OPNsense\Core\Config;
use OPNsense\Firewall\Alias;
use OPNsense\Firewall\Filter;
use OPNsense\Unisense\Shield;

$action = $argv[1] ?? '';
$cnf = Config::getInstance();

try {
    if ($action === 'sync' || $action === 'remove') {
        $cnf->lock();
        $mdl = new Shield();
        if ($action === 'remove' && (string)$mdl->rules === '') {
            // nothing of ours recorded: also look for the aliases by name
            $names = array_keys(Shield::ALIASES);
            $found = false;
            foreach ((new Alias())->aliases->alias->iterateItems() as $node) {
                $found = $found || in_array((string)$node->name, $names);
            }
            if (!$found) {
                $cnf->unlock();
                echo "none" . PHP_EOL;
                exit(0);
            }
        }
        $res = $mdl->syncAndSave($action === 'remove', $action === 'remove' ?
            'UniSense: removed the Shield firewall rules' : 'UniSense Shield: firewall rules updated');
        $cnf->unlock();
        if (count($res['errors'])) {
            fwrite(STDERR, 'Shield rules not valid: ' . implode('; ', $res['errors']) . PHP_EOL);
            exit(1);
        }
        echo ($res['changed'] ? 'changed' : ($action === 'remove' ? 'none' : 'unchanged')) . PHP_EOL;
    } else {
        $mdl = new Shield();
        $rules = [];
        $filter = new Filter();
        foreach ($mdl->ruleIds() as $kind => $uuid) {
            $node = $filter->getNodeByReference('rules.rule.' . $uuid);
            $rules[$kind] = $node === null ? null : [
                'uuid' => $uuid, 'enabled' => (string)$node->enabled === '1', 'action' => (string)$node->action,
                'source' => (string)$node->source_net, 'destination' => (string)$node->destination_net,
                'description' => (string)$node->description,
            ];
        }
        $aliases = [];
        foreach ((new Alias())->aliases->alias->iterateItems() as $node) {
            if (isset(Shield::ALIASES[(string)$node->name])) {
                $aliases[(string)$node->name] = (string)$node->type;
            }
        }
        echo json_encode(['rules' => $rules, 'aliases' => $aliases]) . PHP_EOL;
    }
} catch (Throwable $e) {
    $cnf->unlock();
    fwrite(STDERR, 'could not update the Shield rules: ' . $e->getMessage() . PHP_EOL);
    exit(1);
}
