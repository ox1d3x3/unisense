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

require_once('config.inc');

$current = !empty($config['theme']) ? (string)$config['theme'] : 'opnsense';

if (empty($argv[1])) {
    echo $current . PHP_EOL;
    exit(0);
}

$theme = $argv[1];
if (!preg_match('/^[A-Za-z0-9_.-]+$/', $theme) || !is_dir('/usr/local/opnsense/www/themes/' . $theme)) {
    fwrite(STDERR, "theme not found: {$theme}" . PHP_EOL);
    exit(1);
}

if ($current === $theme) {
    echo "unchanged" . PHP_EOL;
    exit(0);
}

$config['theme'] = $theme;
$result = write_config(sprintf('GUI theme set to %s', $theme));
if (!is_array($result)) {
    fwrite(STDERR, "could not save the configuration" . PHP_EOL);
    exit(1);
}
echo "ok" . PHP_EOL;
