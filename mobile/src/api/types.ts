/**
 * Friendly names for the generated v2 API types. Do not hand-edit shapes
 * here: they come from src/api/schema.d.ts, which is generated from
 * v2/openapi.json (`npm run api:generate`; `npm run api:check` in CI).
 */
import type { components, paths } from "./schema";

type Schemas = components["schemas"];

export type Paths = paths;
export type AuthCheck = Schemas["AuthCheckOut"];
export type Health = Schemas["HealthOut"];
export type ServerSettings = Schemas["SettingsOut"];
export type SettingsPatch = Schemas["SettingsPatch"];
export type Summary = Schemas["SummaryOut"];
export type HistoryDay = Schemas["HistoryDayOut"];
export type Job = Schemas["JobOut"];
export type JobDetail = Schemas["JobDetailOut"];
export type FormStatus = Schemas["FormStatusOut"];
export type FormPreview = Schemas["FormPreviewOut"];
export type BlockedField = Schemas["BlockedFieldOut"];
export type ReviewTask = Schemas["ReviewTaskOut"];
export type ReviewTaskDetail = Schemas["ReviewTaskDetailOut"];
export type ReviewAction = Schemas["ReviewActionOut"];
export type ReviewResolution = Schemas["ReviewResolveIn"]["resolution"];
export type SchedulerStatus = Schemas["SchedulerStatusOut"];
export type Cycle = Schemas["CycleOut"];
export type RunCycle = Schemas["RunCycleOut"];
export type KillSwitch = Schemas["KillSwitchOut"];
export type KillSwitchRequest = Schemas["KillSwitchIn"];
export type Backups = Schemas["BackupsOut"];
export type Backup = Schemas["BackupOut"];
export type Material = Schemas["MaterialOut"];
export type MaterialDetail = Schemas["MaterialDetailOut"];
export type JobMaterials = Schemas["JobMaterialsOut"];
export type MaterialAction = Schemas["MaterialActionOut"];
export type ProfileFact = Schemas["ProfileFactOut"];
export type Profile = Schemas["ProfileOut"];
export type ProfileImport = Schemas["ProfileImportIn"];
export type ProfileImportResult = Schemas["ProfileImportOut"];
export type FactCategory = Schemas["FactCreateIn"]["category"];
export type FactRemoved = Schemas["FactRemovedOut"];
