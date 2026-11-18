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
 * Get or set the GUI theme (System: Settings: General: Theme).
 *
 *   settheme.php            print the current theme
 *   settheme.php NAME       select theme NAME (must exist in www/themes)
 */

/*
 * Uses the MVC configuration API like core's own CLI scripts
 * (firmware/register.php, auth/add_user.php); the legacy write_config()
 * path needs includes (util.inc, ...) that differ between releases.
 */
require_once('script/load_phalcon.php');

use OPNsense\Core\Config;

$cnf = Config::getInstance();
$current = trim((string)$cnf->object()->theme);
if ($current === '') {
    $current = 'opnsense';
}

if (empty($argv[1])) {
    echo $current . PHP_EOL;
    exit(0);
}

$theme = $argv[1];
if (!preg_match('/^[A-Za-z0-9_.-]+$/', $theme) || !is_dir('/usr/local/opnsense/www/themes/' . $theme)) {
    fwrite(STDERR, "theme not found: {$theme}" . PHP_EOL);
    exit(1);
}

try {
    $cnf->lock();   // reloads the file, so nothing written meanwhile is lost
    $root = $cnf->object();
    if (trim((string)$root->theme) === $theme) {
        $cnf->unlock();
        echo "unchanged" . PHP_EOL;
        exit(0);
    }
    $root->theme = $theme;
    $cnf->save(['description' => sprintf('GUI theme set to %s', $theme)]);
    $cnf->unlock();
} catch (Throwable $e) {
    $cnf->unlock();
    fwrite(STDERR, "could not save the configuration: " . $e->getMessage() . PHP_EOL);
    exit(1);
}
echo "ok" . PHP_EOL;
