/**
 * The one file in this directory with no webtree-public equivalent (see
 * ../README.md). webtree-public reads its schemas straight off the wire as
 * untyped JSON, which is exactly what `PublicBlockNode`/`PublicSchemaTree`'s
 * index signatures are shaped for. This app's schemas arrive typed instead —
 * `BuilderElement` (backend/app/models/builder_schema.py) and the
 * `/api/preview/layout` header/footer/body shapes in `@/lib/types` — but at
 * runtime their fields already line up 1:1 with what the vendored renderer
 * reads: `id`/`type`/`styles`/`classes`, and `content` as either an array of
 * children or a field record (schema.ts's `getNodeChildren` and
 * blockRuntime.ts's `getNodeContentRecord` both handle that duality already).
 *
 * So there is no data transform here, only a type-level narrowing from the
 * app's strict shapes down to the renderer's loose ones — a straight `as`
 * cast would work too, but doing it through one named function keeps every
 * call site honest about what's happening instead of scattering casts.
 */
import type { BodySchema, BuilderElement, PreviewFooter, PreviewHeader } from '@/lib/types'
import type { PublicBlockNode, PublicSchemaTree } from './public'

type AdaptableSchema =
  | BuilderElement
  | BodySchema
  | PreviewHeader
  | PreviewFooter
  | PublicBlockNode[]
  | PublicSchemaTree
  | null
  | undefined

export function asPublicSchema(
  schema: AdaptableSchema
): PublicSchemaTree | PublicBlockNode[] | null | undefined {
  return schema as unknown as PublicSchemaTree | PublicBlockNode[] | null | undefined
}
