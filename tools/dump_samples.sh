#!/bin/sh
# usage: dump_samples.sh "ROM path" short_name
python nitrogfx.py dump "$1" "graph_test/$2" --limit 16 > "graph_test/$2.log" 2>/dev/null
for k in bg cell atlas tex raw_bg raw_atlas raw_tex; do python tools/contact_sheet.py "graph_test/$2" $k --max 32 >/dev/null 2>&1; done
