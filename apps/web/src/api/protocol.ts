import { z } from 'zod'

export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue }

export const rpcMetaSchema = z.object({
  idempotencyKey: z.string().min(1).max(260),
  expectedHeadDigest: z.string().nullable().optional(),
  progressToken: z.string().nullable().optional(),
})

export const jsonRpcRequestSchema = z.object({
  jsonrpc: z.literal('2.0').default('2.0'),
  id: z.union([z.string(), z.number().int()]),
  method: z.string().min(1).max(160),
  params: z.object({
    _meta: rpcMetaSchema,
    input: z.record(z.string(), z.unknown()),
  }),
})

export const jsonRpcErrorSchema = z.object({
  code: z.number().int(),
  message: z.string(),
  data: z.record(z.string(), z.unknown()).default({}),
})

export const jsonRpcResponseSchema = z
  .object({
    jsonrpc: z.literal('2.0'),
    id: z.union([z.string(), z.number().int()]).nullable(),
    result: z.record(z.string(), z.unknown()).optional(),
    error: jsonRpcErrorSchema.optional(),
  })
  .refine((value) => Number(value.result !== undefined) + Number(value.error !== undefined) === 1, {
    message: 'exactly one of result or error is required',
  })

export type JsonRpcRequest = z.infer<typeof jsonRpcRequestSchema>
export type JsonRpcResponse = z.infer<typeof jsonRpcResponseSchema>
