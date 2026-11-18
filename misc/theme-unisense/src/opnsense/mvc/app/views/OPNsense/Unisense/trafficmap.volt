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

<div id="utm" class="utm"
     data-worldmap="{{ cache_safe('/ui/js/unisense/worldmap.json') }}"
     data-t-download="{{ lang._('Download') }}"
     data-t-upload="{{ lang._('Upload') }}"
     data-t-countries="{{ lang._('Countries') }}"
     data-t-connections="{{ lang._('Connections') }}"
     data-t-country="{{ lang._('Country') }}"
     data-t-transferred="{{ lang._('Transferred') }}"
     data-t-networks="{{ lang._('Networks') }}"
     data-t-top="{{ lang._('Top 20 countries') }}"
     data-t-live="{{ lang._('Live') }}"
     data-t-paused="{{ lang._('Paused') }}"
     data-t-pause="{{ lang._('Pause') }}"
     data-t-resume="{{ lang._('Resume') }}"
     data-t-unknown="{{ lang._('Unknown') }}"
     data-t-active="{{ lang._('active now') }}"
     data-t-idle="{{ lang._('No traffic to other countries right now.') }}"
     data-t-no-db="{{ lang._('The IP-to-country database has not been downloaded yet. Countries show as unknown until it is.') }}"
     data-t-downloading="{{ lang._('Downloading the IP-to-country databases… this takes about a minute.') }}"
     data-t-download-now="{{ lang._('Download now') }}"
     data-t-update-now="{{ lang._('Update now') }}"
     data-t-updated="{{ lang._('Updated') }}"
     data-t-db="{{ lang._('IP database') }}"
     data-t-schedule="{{ lang._('updates daily at 01:00') }}"
     data-t-error="{{ lang._('Could not read live traffic. Check your privileges or try again.') }}"
     data-t-home="{{ lang._('This firewall') }}"
     data-t-of-total="{{ lang._('of traffic') }}">
    <div class="utm-loading"><i class="fa fa-spinner fa-pulse fa-fw"></i></div>
</div>

<script src="{{ cache_safe('/ui/js/unisense/trafficmap.js') }}"></script>
