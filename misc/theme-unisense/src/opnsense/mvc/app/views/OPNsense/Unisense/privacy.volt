{#
 # Copyright (C) 2026 ox1d3x3
 # All rights reserved.
 #
 # Redistribution and use in source and binary forms, with or without modification,
 # are permitted provided that the following conditions are met:
 #
 # 1. Redistributions of source code must retain the above copyright notice,
 #    this list of conditions and the following disclaimer.
 #
 # 2. Redistributions in binary form must reproduce the above copyright notice,
 #    this list of conditions and the following disclaimer in the documentation
 #    and/or other materials provided with the distribution.
 #
 # THIS SOFTWARE IS PROVIDED "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES,
 # INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
 # AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 # AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
 # OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 # SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 # INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 # CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 # ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 # POSSIBILITY OF SUCH DAMAGE.
 #}

<link rel="stylesheet" type="text/css" href="{{ cache_safe('/ui/css/unisense/trafficmap.css') }}">
<link rel="stylesheet" type="text/css" href="{{ cache_safe('/ui/css/unisense/shield.css') }}">
<link rel="stylesheet" type="text/css" href="{{ cache_safe('/ui/css/unisense/privacy.css') }}">

<div id="upv" class="utm ush upv"
     data-t-off="{{ lang._('Ad blocking off') }}"
     data-t-on-unbound="{{ lang._('Blocking ads and trackers') }}"
     data-t-on-adguard="{{ lang._('AdGuard Home') }}"
     data-t-queries="{{ lang._('DNS queries') }}"
     data-t-blocked="{{ lang._('Blocked') }}"
     data-t-listed="{{ lang._('Domains on block lists') }}"
     data-t-speed="{{ lang._('Average answer') }}"
     data-t-top-client="{{ lang._('Busiest device') }}"
     data-t-last24="{{ lang._('last 24 hours') }}"
     data-t-top-blocked="{{ lang._('Top blocked') }}"
     data-t-top-domains="{{ lang._('Top domains') }}"
     data-t-log="{{ lang._('Query log') }}"
     data-t-setup="{{ lang._('Block ads and trackers for every device') }}"
     data-t-setup-help="{{ lang._('Choose where the blocking happens. Nothing changes in your DNS until you switch it on.') }}"
     data-t-blocking="{{ lang._('Blocking') }}"
     data-t-rules="{{ lang._('Your rules') }}"
     data-t-bypass="{{ lang._('Stop devices going around it') }}"
     data-t-gaming="{{ lang._('Logins, stores, matchmaking, anti-cheat and voice for PlayStation, Xbox, Steam, Battle.net, Ubisoft, Activision, EA, Epic, Riot and Discord are never blocked.') }}"
     data-t-error="{{ lang._('Could not read Privacy. Check your privileges or try again.') }}">
    <div class="utm-loading"><i class="fa fa-spinner fa-pulse fa-fw"></i></div>
</div>

<script src="{{ cache_safe('/ui/js/unisense/privacy.js') }}"></script>
