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

<link rel="stylesheet" type="text/css" href="{{ cache_safe('/ui/css/unisense/networkmap.css') }}">

<div id="usn" class="usn"
     data-t-devices="{{ lang._('Devices') }}"
     data-t-online="{{ lang._('Online') }}"
     data-t-offline="{{ lang._('Offline') }}"
     data-t-networks="{{ lang._('Networks') }}"
     data-t-gateways="{{ lang._('Gateways') }}"
     data-t-internet="{{ lang._('Internet') }}"
     data-t-search="{{ lang._('Search name, IP, MAC or vendor') }}"
     data-t-all="{{ lang._('All') }}"
     data-t-show-offline="{{ lang._('Show offline') }}"
     data-t-live="{{ lang._('Live traffic') }}"
     data-t-refresh="{{ lang._('Refresh') }}"
     data-t-updated="{{ lang._('Updated') }}"
     data-t-no-devices="{{ lang._('No devices found on this network.') }}"
     data-t-no-match="{{ lang._('No devices match your search.') }}"
     data-t-name="{{ lang._('Name') }}"
     data-t-icon="{{ lang._('Icon') }}"
     data-t-ip="{{ lang._('IP address') }}"
     data-t-mac="{{ lang._('MAC address') }}"
     data-t-vendor="{{ lang._('Vendor') }}"
     data-t-hostname="{{ lang._('Hostname') }}"
     data-t-network="{{ lang._('Network') }}"
     data-t-status="{{ lang._('Status') }}"
     data-t-traffic="{{ lang._('Traffic') }}"
     data-t-lease="{{ lang._('DHCP lease') }}"
     data-t-save="{{ lang._('Save') }}"
     data-t-cancel="{{ lang._('Cancel') }}"
     data-t-reset="{{ lang._('Reset') }}"
     data-t-saved="{{ lang._('Saved') }}"
     data-t-error="{{ lang._('Could not load network data. Check your privileges or try again.') }}"
     data-t-save-error="{{ lang._('Could not save the device label.') }}"
     data-t-upstream="{{ lang._('Upstream') }}"
     data-t-firewall="{{ lang._('Firewall') }}">
    <div class="usn-loading"><i class="fa fa-spinner fa-pulse fa-fw"></i></div>
</div>

<script src="{{ cache_safe('/ui/js/unisense/networkmap.js') }}"></script>
