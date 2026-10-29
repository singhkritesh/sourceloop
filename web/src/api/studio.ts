import { http } from './http';
import type {
  DiffResponse, DraftResponse, ProviderRow, ProviderTest, SchemaResponse, StudioConfigValue, ValidationReport, VersionRow,
} from './studioTypes';

const P = '/studio';
export const studio = {
  schema: () => http.get<SchemaResponse>(`${P}/schema`),
  draft: () => http.get<DraftResponse>(`${P}/draft`),
  saveDraft: (config: StudioConfigValue) => http.put<DraftResponse>(`${P}/draft`, { config }),
  validate: (config?: StudioConfigValue) => http.post<ValidationReport>(`${P}/validate`, config ? { config } : {}),
  publish: (note: string) => http.post<{ version: number; content_hash: string }>(`${P}/publish`, { note }),
  versions: () => http.get<VersionRow[]>(`${P}/versions`),
  version: (v: number) => http.get<{ version: number; config: StudioConfigValue } & Record<string, unknown>>(`${P}/versions/${v}`),
  active: () => http.get<{ version: number; config: StudioConfigValue } & Record<string, unknown>>(`${P}/active`),
  rollback: (to_version: number) => http.post<{ version: number; content_hash?: string }>(`${P}/rollback`, { to_version }),
  diff: (a: number, b: number) => http.get<DiffResponse>(`${P}/diff`, { query: { a, b } }),
  providers: () => http.get<ProviderRow[]>(`${P}/providers`),
  setKey: (name: string, secret: string) => http.put<{ has_key: boolean }>(`${P}/providers/${encodeURIComponent(name)}/key`, { secret }),
  deleteKey: (name: string) => http.del<{ has_key: boolean } | void>(`${P}/providers/${encodeURIComponent(name)}/key`),
  testProvider: (name: string) => http.post<ProviderTest>(`${P}/providers/${encodeURIComponent(name)}/test`),
  exportBundle: () => http.get<unknown>(`${P}/export`),
  importBundle: (bundle: unknown) => http.post<unknown>(`${P}/import`, { bundle }),
};
