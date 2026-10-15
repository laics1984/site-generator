/** Synchronize the authored public color math and the preview's style adapter.
 * Run from frontend: node scripts/vendor-color-system.mjs ../../webtree-public [../../builder]
 * The optional builder path also receives its numerical parity fixtures.
 */
import { readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const publicRepo = process.argv[2] ?? '../../webtree-public'
const source = readFileSync(join(publicRepo, 'lib/colorSystem.ts'), 'utf8')
writeFileSync('src/preview/lib/colorSystem.ts', source)
const adapter = readFileSync(join(publicRepo, 'lib/styles.ts'), 'utf8')
writeFileSync('src/preview/lib/styles.ts',
  '// Vendored from webtree-public/lib/styles.ts — near-verbatim, only the\n' +
  '// import path changed (`~/types/public` → `./public`).\n' +
  adapter.replace("from '~/types/public'", "from './public'"))
if (process.argv[3]) {
  writeFileSync(join(process.argv[3], 'src/lib/color-system.ts'), source)
  writeFileSync(join(process.argv[3], 'src/lib/color-system.fixtures.json'),
    readFileSync(join(publicRepo, 'lib/colorSystem.fixtures.json'), 'utf8'))
}
console.log('Synchronized semantic color math and preview style adapter')
