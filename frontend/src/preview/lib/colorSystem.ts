/** Authored semantic color math. Vendored by the builder and generator preview.
 * Python parity: site-generator/app/services/color_system.py. CSS Color 4
 * OKLab matrices; chroma reduction keeps derived sRGB colors in gamut.
 */
export type ThemeColors = Record<'primary' | 'secondary' | 'accent' | 'text' | 'background' | 'surface', string>
type Triple = [number, number, number]
const fields = ['primary', 'secondary', 'accent', 'text', 'background', 'surface'] as const
export const normalizeHex = (value: unknown): string | null => {
  if (typeof value !== 'string') return null
  let s = value.trim().toLowerCase()
  if (/^#[0-9a-f]{3}$/.test(s)) s = '#' + [...s.slice(1)].map(c => c+c).join('')
  return /^#[0-9a-f]{6}$/.test(s) ? s : null
}
const rgb = (s: string): Triple => [1, 3, 5].map(i => parseInt(s.slice(i, i+2), 16)/255) as Triple
const linear = (v: number) => v <= .04045 ? v/12.92 : ((v+.055)/1.055)**2.4
export const colorLuminance = (s: string): number => {
  const [r,g,b] = rgb(s).map(linear)
  return .2126*r+.7152*g+.0722*b
}
export const colorContrast = (a: string, b: string): number => {
  const x = colorLuminance(a), y = colorLuminance(b)
  return (Math.max(x,y)+.05)/(Math.min(x,y)+.05)
}
export const toOklch = (s: string): Triple => {
  const [r,g,b] = rgb(s).map(linear)
  const l = Math.cbrt(.4122214708*r+.5363325363*g+.0514459929*b)
  const m = Math.cbrt(.2119034982*r+.6806995451*g+.1073969566*b)
  const z = Math.cbrt(.0883024619*r+.2817188376*g+.6299787005*b)
  const light = .2104542553*l+.793617785*m-.0040720468*z
  const a = 1.9779984951*l-2.428592205*m+.4505937099*z
  const b_ = .0259040371*l+.7827717662*m-.808675766*z
  return [light, Math.hypot(a,b_), Math.atan2(b_,a)]
}
const linearRgb = (light: number, chroma: number, hue: number): Triple => {
  const a = chroma*Math.cos(hue), b = chroma*Math.sin(hue)
  const l = (light+.3963377774*a+.2158037573*b)**3
  const m = (light-.1055613458*a-.0638541728*b)**3
  const s = (light-.0894841775*a-1.291485548*b)**3
  return [4.0767416621*l-3.3077115913*m+.2309699292*s,
    -1.2684380046*l+2.6097574011*m-.3413193965*s,
    -.0041960863*l-.7034186147*m+1.707614701*s]
}
export const fromOklch = (light: number, chroma: number, hue: number): string => {
  light = Math.max(0,Math.min(1,light)); chroma = Math.max(0,chroma)
  const inGamut = (v: Triple) => v.every(c => c >= -1e-7 && c <= 1+1e-7)
  let channels = linearRgb(light,chroma,hue)
  if (!inGamut(channels)) {
    let lo = 0, hi = chroma
    for (let i=0;i<24;i++) {
      const mid = (lo+hi)/2
      if (inGamut(linearRgb(light,mid,hue))) lo=mid; else hi=mid
    }
    channels = linearRgb(light,lo,hue)
  }
  return '#'+channels.map(v => {
    const c = Math.max(0,Math.min(1,v))
    return Math.round((c<=.0031308 ? 12.92*c : 1.055*c**(1/2.4)-.055)*255).toString(16).padStart(2,'0')
  }).join('')
}
const tone = (s: string, light: number, chroma?: number) => {
  const [,c,h] = toOklch(s)
  return fromOklch(light,chroma??c,h)
}
export const mixPerceptual = (a: string, b: string, amount: number): string => {
  const [al,ac,ah] = toOklch(a), [bl,bc,bh] = toOklch(b), t = Math.max(0,Math.min(1,amount))
  const x = ac*Math.cos(ah)*(1-t)+bc*Math.cos(bh)*t
  const y = ac*Math.sin(ah)*(1-t)+bc*Math.sin(bh)*t
  return fromOklch(al*(1-t)+bl*t,Math.hypot(x,y),Math.atan2(y,x))
}
export const ensureColorContrast = (fg: string, bg: string, target=4.5): string => {
  if (colorContrast(fg,bg)>=target) return fg
  const [l,c,h] = toOklch(fg)
  const candidates: Array<[number,string]> = []
  for (const end of [0,1]) {
    if (colorContrast(fromOklch(end,c,h),bg)<target) continue
    let lo=0, hi=1
    for (let i=0;i<24;i++) {
      const t=(lo+hi)/2
      if (colorContrast(fromOklch(l+(end-l)*t,c,h),bg)>=target) hi=t; else lo=t
    }
    const value=fromOklch(l+(end-l)*hi,c,h)
    candidates.push([Math.abs(toOklch(value)[0]-l),value])
  }
  if (candidates.length) return candidates.sort((a,b)=>a[0]-b[0])[0][1]
  return colorContrast('#000000',bg)>=colorContrast('#ffffff',bg) ? '#000000' : '#ffffff'
}
const readable = (bg: string, dark: string, light: string) =>
  ensureColorContrast(colorContrast(dark,bg)>=colorContrast(light,bg) ? dark : light,bg)
const cache = new Map<string, Readonly<Record<string,string>>>()
export function deriveThemeColors(input: ThemeColors): Readonly<Record<string,string>> {
  const values = fields.map(k => normalizeHex(input[k]))
  if (values.some(v=>!v)) return Object.freeze({})
  const key=values.join('|'), cached=cache.get(key)
  if (cached) return cached
  const p=Object.fromEntries(fields.map((k,i)=>[k,values[i]!])) as ThemeColors
  const bg=p.background, surface=p.surface, darkPage=colorLuminance(bg)<.2
  const dark=tone(p.secondary,.16,.022), light=tone(surface,.985,.008)
  const out: Record<string,string>={...p,
    'surface-inverse':dark,'ink-dark':dark,'ink-light':light,'text-inverse':readable(dark,dark,light),
    'surface-elevated':tone(surface,Math.min(.995,toOklch(surface)[0]+(darkPage?.04:.012))),
    'surface-muted':mixPerceptual(bg,p.secondary,darkPage?.08:.045),
    'surface-alternate':mixPerceptual(surface,p.secondary,darkPage?.10:.055),
    'photo-ink':'#ffffff','photo-ink-dark':'#000000','photo-scrim':'#101820','photo-wash':'#ffffff'}
  out.text=ensureColorContrast(p.text,bg); out.heading=out.text
  out['text-muted']=ensureColorContrast(mixPerceptual(out.text,bg,.28),bg)
  out.border=mixPerceptual(bg,out.text,.18)
  out['border-strong']=ensureColorContrast(mixPerceptual(bg,out.text,.40),bg,3)
  for (const name of ['background','surface','surface-elevated','surface-muted','surface-alternate','surface-inverse']) out['on-'+name]=readable(out[name],dark,light)
  for (const name of ['primary','secondary','accent'] as const) {
    const color=p[name], [l,c,h]=toOklch(color), direction=l<.4?.045:-.045
    out[name+'-hover']=fromOklch(l+direction,c,h); out[name+'-active']=fromOklch(l+direction*1.8,c,h)
    out[name+'-ink']=ensureColorContrast(color,bg)
    out[name+'-tint']=mixPerceptual(bg,color,darkPage?.16:.10)
    out[name+'-tint-ink']=ensureColorContrast(color,out[name+'-tint'])
    out['on-'+name+'-tint']=readable(out[name+'-tint'],dark,light)
    out[name+'-border']=ensureColorContrast(color,bg,3)
    for (const suffix of ['','-hover','-active']) out['on-'+name+suffix]=readable(out[name+suffix],dark,light)
  }
  ;[p.primary,p.secondary,p.accent,p.surface].forEach((color,i)=>{
    out['band-'+(i+1)]=mixPerceptual(bg,color,darkPage?.15:.075)
    out['on-band-'+(i+1)]=readable(out['band-'+(i+1)],dark,light)
  })
  for (const surface of ['background','surface','surface-inverse','primary','secondary','accent','band-1','band-2','band-3','band-4']) {
    out['error-ink-on-'+surface]=ensureColorContrast('#dc2626',out[surface])
    for (const brand of ['primary','secondary','accent'] as const) {
      out[brand+'-ink-on-'+surface]=ensureColorContrast(p[brand],out[surface])
      out[brand+'-border-on-'+surface]=ensureColorContrast(p[brand],out[surface],3)
    }
  }
  const result=Object.freeze(out)
  if(cache.size>=256) cache.delete(cache.keys().next().value!)
  cache.set(key,result)
  return result
}
export const semanticColorVars = (colors: ThemeColors, buttons?: { background: string; text: string }): Record<string,string> => {
  const vars = Object.fromEntries(Object.entries(deriveThemeColors(colors)).map(([k,v])=>['--builder-color-'+k,v]))
  const background = normalizeHex(buttons?.background)
  if (background) {
    const states = deriveThemeColors({...colors,primary:background})
    vars['--builder-button-text'] = ensureColorContrast(normalizeHex(buttons?.text) || states['on-primary'],background)
    for (const state of ['hover','active']) {
      vars['--builder-button-'+state] = states['primary-'+state]
      vars['--builder-button-on-'+state] = states['on-primary-'+state]
    }
  }
  return vars
}
