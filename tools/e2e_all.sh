#!/bin/sh
# ROM location: try the old flat layout first (../NAME/*.nds), then the
# "jogos testados" folder the user reorganized ROMs into later.
mkdir -p graph_test
run() {
    rom=$(ls "../$1"/*.nds 2>/dev/null || ls "../jogos testados/$1"/*.nds 2>/dev/null || ls "../$1.nds" 2>/dev/null)
    if [ -z "$rom" ]; then
        echo "ROM not found: $1" > "graph_test/$2.e2e.log"
        return
    fi
    python tools/e2e_test.py "$rom" "graph_test/$2" --per-kind 3 ${3:+--filter "$3"} > "graph_test/$2.e2e.log" 2>&1
}
run "Chocobo to Mahou no Ehon - Majo to Shoujo to 5-nin no Yuusha (Japan)" chocobo_ehon "book/0[12]/*" &
run "Elite Beat Agents (USA)" elite_beat_agents "data/2d/*" &
run "Lufia - Curse of the Sinistrals (USA)" lufia_cots &
run "Rune Factory 3 - A Fantasy Harvest Moon (USA)" rune_factory_3 &
run "Suikoden - Tierkreis (USA) (EnFrEs)" suikoden_tierkreis "rom/scenario/*" &
run "World Ends with You The (USA)" twewy "apl_mor/*" &
run "Luminous Arc (USA)" luminous_arc "data/a5*" &
run "Etrian Odyssey (USA)" etrian_odyssey "data/*" &
wait
run "Ni No Kuni - The Jet Black Mage (Japan) (T-En by Anjiera v1.0) (n)" ni_no_kuni "data/battle/*" &
run "Harvest Moon DS - Island of Happiness (USA)" harvest_moon_ioh "*.xbb" &
run "Okamiden (USA)" okamiden "rom/ending/*" &
run "Wizard of Oz The - Beyond the Yellow Brick Road (USA)" wizard_of_oz "data/b0*" &
run "Nostalgia (USA)" nostalgia &
run "Drawn to Life (USA) (EnFr)" drawn_to_life "challenge/world1/*" &
wait
run "Kimi no Yuusha (Japan)" kimi_no_yuusha &
run "Soma Bringer (Japan)" soma_bringer &
run "Nine Hours Nine Persons Nine Doors (USA)" 999 &
run "Solatorobo - Red the Hunter (USA) (EnFrDeEsIt) (NDSi Enhanced)" solatorobo &
run "Radiant Historia (USA)" radiant_historia &
wait
run "Luminous Arc 3 - Eyes (Japan) (T-En by Asakura and Buster Gundo and CheshireCat and Jaumander and Plasturion v1.0)" la3 "bg/*" &
run "Magical Starsign (USA)" magical_starsign &
run "Valkyrie Profile - Covenant of the Plume (USA)" vp_cpk &
run "Sigma Harmonics (Japan)" sigma_harmonics &
run "Professor Layton and the Curious Village (USA)" layton1 "data/*" &
wait
