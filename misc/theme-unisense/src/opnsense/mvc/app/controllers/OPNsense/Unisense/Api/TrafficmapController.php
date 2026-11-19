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

/**
 * Traffic Map: live traffic per country (configd: unisense trafficmap ...)
 * @package OPNsense\Unisense
 */
class TrafficmapController extends ApiControllerBase
{
    private function runJson($action, $timeout = 60)
    {
        $out = trim((new Backend())->configdRun($action, false, $timeout));
        $data = json_decode($out, true);
        if (!is_array($data)) {
            return ['ok' => false, 'error' => $out === '' ? 'no response from configd' : substr($out, 0, 300)];
        }
        return $data;
    }

    /**
     * live traffic per country (rates since the previous call)
     */
    public function flowsAction()
    {
        return $this->runJson('unisense trafficmap flows');
    }

    /**
     * every country seen since the history started (bytes, connections, last seen)
     */
    public function historyAction()
    {
        return $this->runJson('unisense trafficmap history', 30);
    }

    /**
     * start the country history afresh
     */
    public function resetAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed', 'error' => 'POST required'];
        }
        return $this->runJson('unisense trafficmap reset', 30);
    }

    /**
     * IP database status
     */
    public function statusAction()
    {
        return $this->runJson('unisense trafficmap status', 20);
    }

    /**
     * download / rebuild the IP databases in the background
     */
    public function updateAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed', 'error' => 'POST required'];
        }
        (new Backend())->configdRun('unisense geoip update', true);
        return ['status' => 'started'];
    }
}
