// Albers USA (lower 48 + Alaska and Hawaii insets), matching d3.geoAlbersUsa().scale(1300).translate([487.5, 305])
// used to draw public/docket-*.svg (viewBox 975x610). Returns pin position as % of width/height.
const RAD = Math.PI / 180;

function conic(parallels: [number, number], rotate: number, center: [number, number], k: number, t: [number, number]) {
  const sy0 = Math.sin(parallels[0] * RAD);
  const n = (sy0 + Math.sin(parallels[1] * RAD)) / 2;
  const c = 1 + sy0 * (2 * n - sy0);
  const r0 = Math.sqrt(c) / n;
  const raw = (l: number, p: number): [number, number] => {
    const r = Math.sqrt(c - 2 * n * Math.sin(p)) / n;
    return [r * Math.sin(l * n), r0 - r * Math.cos(l * n)];
  };
  const [cx, cy] = raw(center[0] * RAD, center[1] * RAD);
  return (lon: number, lat: number): [number, number] => {
    let l = (lon + rotate) * RAD;
    l = Math.atan2(Math.sin(l), Math.cos(l)); // wrap to [-pi, pi]
    const [x, y] = raw(l, lat * RAD);
    return [t[0] + k * (x - cx), t[1] - k * (y - cy)];
  };
}

const K = 1300, T: [number, number] = [487.5, 305];
const lower48 = conic([29.5, 45.5], 96, [-0.6, 38.7], K, T);
const alaska = conic([55, 65], 154, [-2, 58.5], K * 0.35, [T[0] - 0.307 * K, T[1] + 0.201 * K]);
const hawaii = conic([8, 18], 157, [-3, 19.9], K, [T[0] - 0.205 * K, T[1] + 0.212 * K]);

export function pin(lat?: number | null, lon?: number | null): { left: number; top: number } | null {
  if (lat == null || lon == null) return null;
  const p = lat > 50 && lon < -129 ? alaska(lon, lat) : lat < 23 && lon < -154 && lon > -161 ? hawaii(lon, lat) : lower48(lon, lat);
  const left = (p[0] / 975) * 100, top = (p[1] / 610) * 100;
  return left < 0 || left > 100 || top < 0 || top > 100 ? null : { left, top };
}
