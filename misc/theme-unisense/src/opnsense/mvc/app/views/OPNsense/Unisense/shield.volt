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

<div id="ush" class="utm ush"
     data-t-on="{{ lang._('Protected') }}"
     data-t-off="{{ lang._('Threat lists off') }}"
     data-t-update-now="{{ lang._('Update lists now') }}"
     data-t-updating="{{ lang._('Updating…') }}"
     data-t-updated="{{ lang._('Lists updated') }}"
     data-t-schedule="{{ lang._('checked every 15 minutes') }}"
     data-t-attacks="{{ lang._('Blocked from the Internet') }}"
     data-t-stopped="{{ lang._('Your devices stopped') }}"
     data-t-alerts="{{ lang._('Alerts') }}"
     data-t-listed="{{ lang._('Addresses on block lists') }}"
     data-t-today="{{ lang._('today') }}"
     data-t-this-week="{{ lang._('this week') }}"
     data-t-alerts-title="{{ lang._('Devices that tried to reach a known-bad address') }}"
     data-t-alerts-help="{{ lang._('Shield refused these connections. A device that keeps trying may be infected: check it, update it, or scan it.') }}"
     data-t-mark-seen="{{ lang._('Mark as seen') }}"
     data-t-recent="{{ lang._('Recent blocks') }}"
     data-t-no-blocks="{{ lang._('Nothing blocked yet.') }}"
     data-t-lists="{{ lang._('Threat lists') }}"
     data-t-lists-help="{{ lang._('Free public lists of botnet servers, criminal networks and attackers, refreshed automatically.') }}"
     data-t-both="{{ lang._('Both ways') }}"
     data-t-inbound="{{ lang._('Inbound only') }}"
     data-t-countries="{{ lang._('Blocked countries') }}"
     data-t-countries-help="{{ lang._('Connections from these countries are dropped, and your devices cannot connect to them.') }}"
     data-t-no-countries="{{ lang._('No country is blocked.') }}"
     data-t-block="{{ lang._('Block') }}"
     data-t-unblock="{{ lang._('Unblock') }}"
     data-t-pick-country="{{ lang._('Country to block') }}"
     data-t-check="{{ lang._('Check an address') }}"
     data-t-check-help="{{ lang._('Country, network, block lists and reputation of any IP address.') }}"
     data-t-allow="{{ lang._('Allowed addresses') }}"
     data-t-allow-help="{{ lang._('Addresses Shield lets through even when a list or a blocked country includes them. Use Allow on a block or alert for a set time; the box below is for addresses that should never be blocked (one per line).') }}"
     data-t-allow-btn="{{ lang._('Allow this address…') }}"
     data-t-keys="{{ lang._('Reputation lookups') }}"
     data-t-keys-help="{{ lang._('Optional free API keys: the address check then also shows VirusTotal and AbuseIPDB scores. Keys stay on the firewall.') }}"
     data-t-save="{{ lang._('Save') }}"
     data-t-saved="{{ lang._('Saved') }}"
     data-t-checkup="{{ lang._('Security checkup') }}"
     data-t-gaming="{{ lang._('Gaming-safe: Shield only adds lookups in the firewall address tables. Nothing is inspected or slowed down, and scanner lists never touch your outgoing connections.') }}"
     data-t-error="{{ lang._('Could not read Shield. Check your privileges or try again.') }}">
    <div class="utm-loading"><i class="fa fa-spinner fa-pulse fa-fw"></i></div>
</div>

<script src="{{ cache_safe('/ui/js/unisense/shield.js') }}"></script>
