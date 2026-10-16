import fs from 'fs';
import {geoAlbersUsa, geoPath} from 'd3-geo';
import {feature, mesh} from 'topojson-client';
const us = JSON.parse(fs.readFileSync('node_modules/us-atlas/states-albers-10m.json'));
const path = geoPath(null);
const nation = path(feature(us, us.objects.nation));
const borders = path(mesh(us, us.objects.states, (a,b)=>a!==b));
const r = s => s.replace(/(\d+\.\d{1})\d+/g,'$1');
const svg = (land, line) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 975 610" width="975" height="610"><path d="${r(nation)}" fill="${land}" stroke="${line}" stroke-width="0.8"/><path d="${r(borders)}" fill="none" stroke="${line}" stroke-width="0.6"/></svg>`;
fs.writeFileSync('docket-light.svg', svg('#ECECE9','#BDBDB8'));
fs.writeFileSync('docket-dark.svg', svg('#1E1E1E','#3A3A3A'));
const proj = geoAlbersUsa().scale(1300).translate([487.5,305]);
const pts = JSON.parse(fs.readFileSync('points.json'));
for (const p of pts) { if (p.lat==null) continue; const xy = proj([p.lon,p.lat]); p.x = xy? +(xy[0]/975*100).toFixed(2):null; p.y = xy? +(xy[1]/610*100).toFixed(2):null; }
const extra = {Akron:[-81.4236,41.0547], Mosby:[-94.2975,39.3567]};
for (const [k,v] of Object.entries(extra)) { const xy=proj(v); console.log(k,(xy[0]/975*100).toFixed(2),(xy[1]/610*100).toFixed(2)); }
fs.writeFileSync('points2.json', JSON.stringify(pts));
for (const p of pts) console.log(p.id,p.city,p.x,p.y,p.fatal);
