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
 * Daily update of the Traffic Map's IP databases as a regular OPNsense cron
 * job (System > Settings > Cron, origin "UniSense"), 01:00 local time.
 *
 *   setup_cron.php install   create it if missing (an existing job, possibly
 *                            edited by the user, is left alone)
 *   setup_cron.php remove    delete it
 *   setup_cron.php           print the job's schedule, or "none"
 *
 * configd must know "unisense geoip update" (restart configd after installing
 * actions_unisense.conf): the cron model validates commands against it.
 */
require_once('script/load_phalcon.php');

use OPNsense\Core\Config;
use OPNsense\Cron\Cron;

const ORIGIN = 'UniSense';
/* command => [description, minutes, hours] */
const JOBS = [
    'unisense geoip update' => ['UniSense: update IP-to-country databases (Traffic Map)', '0', '1'],
    'unisense trafficmap sample' => ['UniSense: record Traffic Map history (every minute)', '*', '*'],
];

function find_jobs($mdl)
{
    $found = [];
    foreach ($mdl->jobs->job->iterateItems() as $uuid => $job) {
        if ((string)$job->origin === ORIGIN || isset(JOBS[(string)$job->command])) {
            $found[$uuid] = $job;
        }
    }
    return $found;
}

$action = $argv[1] ?? '';
$cnf = Config::getInstance();

try {
    $cnf->lock();
    $mdl = new Cron();
    $jobs = find_jobs($mdl);

    if ($action === 'install') {
        $have = [];
        foreach ($jobs as $job) {
            $have[(string)$job->command] = true;
        }
        $added = 0;
        foreach (JOBS as $command => [$descr, $minutes, $hours]) {
            if (isset($have[$command])) {
                continue;
            }
            $uuid = $mdl->newDailyJob(ORIGIN, $command, $descr, '*', '1');
            $job = $mdl->getNodeByReference('jobs.job.' . $uuid);
            $job->minutes = $minutes;
            $job->hours = $hours;
            $added++;
        }
        if (!$added) {
            $cnf->unlock();
            echo "exists" . PHP_EOL;
            exit(0);
        }
        $errors = $mdl->performValidation();
        if (count($errors)) {
            $msg = [];
            foreach ($errors as $e) {
                $msg[] = $e->getField() . ': ' . $e->getMessage();
            }
            $cnf->unlock();
            fwrite(STDERR, 'cron job not valid: ' . implode('; ', $msg) . PHP_EOL);
            exit(1);
        }
        $mdl->serializeToConfig();
        $cnf->save(['description' => 'UniSense: Traffic Map jobs (IP database at 01:00, history every minute)']);
        $cnf->unlock();
        echo "ok" . PHP_EOL;
    } elseif ($action === 'remove') {
        if (!count($jobs)) {
            $cnf->unlock();
            echo "none" . PHP_EOL;
            exit(0);
        }
        foreach (array_keys($jobs) as $uuid) {
            $mdl->jobs->job->del($uuid);
        }
        $mdl->serializeToConfig(false, true);
        $cnf->save(['description' => 'UniSense: removed the Traffic Map jobs']);
        $cnf->unlock();
        echo "ok" . PHP_EOL;
    } else {
        $cnf->unlock();
        if (!count($jobs)) {
            echo "none" . PHP_EOL;
        }
        foreach ($jobs as $job) {
            printf("%s %s * * %s %s%s\n", $job->minutes, $job->hours, $job->weekdays, $job->command,
                (string)$job->enabled === '1' ? '' : ' (disabled)');
        }
    }
} catch (Throwable $e) {
    $cnf->unlock();
    fwrite(STDERR, 'could not update the cron jobs: ' . $e->getMessage() . PHP_EOL);
    exit(1);
}
