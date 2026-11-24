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
use OPNsense\Firewall\Alias;
use OPNsense\Firewall\Filter;

/**
 * UniSense Shield: the settings, and the aliases + firewall rules that do the
 * blocking (in Firewall > Rules / Automation, marked "UniSense Shield").
 *
 * Three "External" aliases are pf tables filled by scripts/unisense/shield.py.
 * Five quick floating rules use them, all on traffic entering an interface
 * (direction "in" works on every supported core):
 *   - from the Internet: the source is in a table   -> block (drop)
 *   - from your devices: the destination is in one  -> reject (instant error)
 * Scanner lists are only used inbound.
 * @package OPNsense\Unisense
 */
class Shield extends BaseModel
{
    const ALIASES = [
        'unisense_geoblock' => 'UniSense Shield: blocked countries',
        'unisense_threats' => 'UniSense Shield: botnet and criminal networks',
        'unisense_scanners' => 'UniSense Shield: scanners and attackers (inbound)',
    ];

    /* kind => [alias, source or destination, action, description] */
    const RULES = [
        'country-in' => ['unisense_geoblock', 'source_net', 'block', 'UniSense Shield: blocked countries (from the Internet)'],
        'country-out' => ['unisense_geoblock', 'destination_net', 'reject', 'UniSense Shield: blocked countries (your devices)'],
        'threat-in' => ['unisense_threats', 'source_net', 'block', 'UniSense Shield: threat lists (from the Internet)'],
        'threat-out' => ['unisense_threats', 'destination_net', 'reject', 'UniSense Shield: threat lists (your devices)'],
        'scanner-in' => ['unisense_scanners', 'source_net', 'block', 'UniSense Shield: scanner lists (from the Internet)'],
    ];

    /**
     * @return array kind => uuid of the rules this model created
     */
    public function ruleIds()
    {
        $res = [];
        foreach (explode(',', (string)$this->rules) as $item) {
            $parts = explode(':', $item, 2);
            if (count($parts) == 2 && isset(self::RULES[$parts[0]])) {
                $res[$parts[0]] = $parts[1];
            }
        }
        return $res;
    }

    /**
     * which rules should be on for the current settings
     */
    public function wanted($kind)
    {
        if (strpos($kind, 'country') === 0) {
            return trim((string)$this->countries) !== '';
        }
        return (string)$this->enabled === '1';
    }

    /**
     * Create, update or remove the aliases and rules, and save. Runs under the
     * caller's config lock. Two saves, because a rule's alias is validated
     * against the saved configuration: aliases before the rules that use them
     * (and rules before their aliases when removing).
     * @param bool $remove take everything out (uninstall)
     * @return array ['changed' => bool, 'errors' => []]
     */
    public function syncAndSave($remove = false, $description = 'UniSense Shield: settings saved')
    {
        $cnf = Config::getInstance();
        if ($remove) {
            $changed = $this->removeRules();
            if ($changed) {
                $cnf->save(['description' => $description]);
            }
            if ($this->removeAliases()) {
                $cnf->save(['description' => $description]);
                $changed = true;
            }
            return ['changed' => $changed, 'aliases' => $changed, 'errors' => []];
        }
        // the first time only: the aliases, saved on their own (see above)
        $aliases = $this->ensureAliases();
        if ($aliases) {
            $cnf->save(['description' => 'UniSense Shield: block-list aliases']);
        }
        $errors = [];
        $changed = $this->ensureRules($errors);
        if (count($errors)) {
            return ['changed' => false, 'aliases' => $aliases, 'errors' => $errors];
        }
        // the settings and the rules: one revision
        $this->serializeToConfig(false, true);
        $cnf->save(['description' => $description]);
        return ['changed' => $changed || $aliases, 'aliases' => $aliases, 'errors' => []];
    }

    /**
     * @return bool something changed (and was serialized)
     */
    private function ensureAliases()
    {
        $changed = false;
        $alias = new Alias();
        $have = [];
        foreach ($alias->aliases->alias->iterateItems() as $node) {
            $have[(string)$node->name] = $node;
        }
        foreach (self::ALIASES as $name => $descr) {
            $node = $have[$name] ?? null;
            if ($node === null) {
                $node = $alias->aliases->alias->Add();
                $node->setNodes(['enabled' => '1', 'name' => $name, 'type' => 'external', 'description' => $descr]);
                $changed = true;
            } elseif ((string)$node->type !== 'external' || (string)$node->enabled !== '1') {
                $node->type = 'external';
                $node->enabled = '1';
                $changed = true;
            }
        }
        if ($changed) {
            $alias->serializeToConfig(false, true);
        }
        return $changed;
    }

    /**
     * one rule per kind, recreated if deleted, switched with the settings
     * @return bool something changed (and was serialized, with this model)
     */
    private function ensureRules(&$errors)
    {
        $changed = false;
        $filter = new Filter();
        $ids = $this->ruleIds();
        $nodes = [];
        foreach ($filter->rules->rule->iterateItems() as $uuid => $node) {
            $nodes[$uuid] = $node;
        }
        $new = [];
        $seq = 1;
        foreach (self::RULES as $kind => [$table, $field, $action, $descr]) {
            $want = [
                'enabled' => $this->wanted($kind) ? '1' : '0',
                'action' => $action,
                'quick' => '1',
                'interface' => '',
                'direction' => 'in',
                'ipprotocol' => 'inet46',
                'protocol' => 'any',
                'source_net' => $field === 'source_net' ? $table : 'any',
                'destination_net' => $field === 'destination_net' ? $table : 'any',
                'log' => '1',
                'description' => $descr,
            ];
            $uuid = $ids[$kind] ?? null;
            $node = $uuid !== null ? ($nodes[$uuid] ?? null) : null;
            if ($node === null) {
                $node = $filter->rules->rule->Add();
                $node->setNodes(array_merge($want, ['sequence' => (string)$seq]));
                $uuid = $node->getAttribute('uuid');
                $changed = true;
            } else {
                // only what Shield decides; anything else the user changed stays
                foreach (['enabled', 'action', 'source_net', 'destination_net'] as $key) {
                    if ((string)$node->$key !== $want[$key]) {
                        $node->$key = $want[$key];
                        $changed = true;
                    }
                }
            }
            $new[] = $kind . ':' . $uuid;
            $seq++;
        }
        $this->rules = implode(',', $new);
        if (!$changed) {
            return false;
        }
        foreach ($filter->performValidation() as $msg) {
            $errors[] = $msg->getField() . ': ' . $msg->getMessage();
        }
        if (count($errors)) {
            return false;
        }
        $filter->serializeToConfig(false, true);
        return true;
    }

    private function removeRules()
    {
        $filter = new Filter();
        $changed = false;
        foreach ($this->ruleIds() as $uuid) {
            if ($filter->rules->rule->del($uuid)) {
                $changed = true;
            }
        }
        $this->rules = '';
        if ($changed) {
            $filter->serializeToConfig(false, true);
        }
        $this->serializeToConfig(false, true);
        return $changed;
    }

    private function removeAliases()
    {
        // an alias still used by a rule (one the user made) stays
        $used = [];
        foreach ((new Filter())->rules->rule->iterateItems() as $rule) {
            foreach (explode(',', (string)$rule->source_net . ',' . (string)$rule->destination_net) as $n) {
                $used[$n] = true;
            }
        }
        $alias = new Alias();
        $changed = false;
        foreach ($alias->aliases->alias->iterateItems() as $uuid => $node) {
            if (isset(self::ALIASES[(string)$node->name]) && !isset($used[(string)$node->name])) {
                $alias->aliases->alias->del($uuid);
                $changed = true;
            }
        }
        if ($changed) {
            $alias->serializeToConfig(false, true);
        }
        return $changed;
    }
}
