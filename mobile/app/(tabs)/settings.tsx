import { useRouter } from "expo-router";
import { useEffect, useMemo, useState } from "react";
import { RefreshControl, ScrollView, StyleSheet, Switch, Text, TextInput, View } from "react-native";

import { api, describeError } from "@/api/client";
import type { Backups, ServerSettings } from "@/api/types";
import { Banner } from "@/components/Banner";
import { Card, Muted, Row } from "@/components/Card";
import { LockBanner } from "@/components/LockBanner";
import { PrimaryButton } from "@/components/PrimaryButton";
import { ScreenContainer } from "@/components/ScreenContainer";
import { ErrorView, LoadingView } from "@/components/StatusViews";
import { useApiResource } from "@/hooks/useApiResource";
import { useConnection } from "@/store/connection";
import { useTheme } from "@/theme";
import { formatBytes, formatDateTime, parseList } from "@/utils/format";
import { buildSettingsPatch, settingsToForm, type SettingsForm } from "@/settingsForm";

type SettingsData = { settings: ServerSettings; backups: Backups };

async function loadSettings(): Promise<SettingsData> {
  const [settings, backups] = await Promise.all([api.settings(), api.backups()]);
  return { settings, backups };
}

type NumberKey = "max_dry_runs_per_day" | "max_applications_per_day" | "min_match_score" | "cycle_interval_minutes" | "cycle_max_dry_runs" | "cycle_max_prepare" | "cycle_max_score";

const NUMBER_FIELDS: { key: NumberKey; label: string; hint: string }[] = [
  { key: "max_dry_runs_per_day", label: "Dry-runs per day", hint: "0-100" },
  { key: "max_applications_per_day", label: "Applications per day", hint: "0-50 (submission is locked)" },
  { key: "min_match_score", label: "Minimum match score", hint: "0-100" },
  { key: "cycle_interval_minutes", label: "Cycle interval (min)", hint: "5-1440" },
  { key: "cycle_max_dry_runs", label: "Dry-runs per cycle", hint: "0-20" },
  { key: "cycle_max_prepare", label: "Drafts per cycle", hint: "0-20" },
  { key: "cycle_max_score", label: "Jobs scored per cycle", hint: "0-500" },
];

type ListKey =
  | "target_keywords"
  | "target_locations"
  | "excluded_titles"
  | "excluded_locations"
  | "blacklisted_companies"
  | "greenhouse_board_tokens"
  | "lever_companies"
  | "ashby_orgs";

interface ListField {
  key: ListKey;
  label: string;
  hint?: string;
}

const LIST_FIELDS: ListField[] = [
  { key: "target_keywords", label: "Target keywords" },
  { key: "target_locations", label: "Target locations" },
  { key: "excluded_titles", label: "Excluded titles" },
  { key: "excluded_locations", label: "Excluded locations" },
  { key: "blacklisted_companies", label: "Excluded employers" },
];

const SOURCE_FIELDS: ListField[] = [
  { key: "greenhouse_board_tokens", label: "Greenhouse boards", hint: "board token from boards.greenhouse.io/<token>" },
  { key: "lever_companies", label: "Lever companies", hint: "slug from jobs.lever.co/<slug>" },
  { key: "ashby_orgs", label: "Ashby organizations", hint: "slug from jobs.ashbyhq.com/<slug>" },
];

type FormProps = { form: SettingsForm; set: ReturnType<typeof useSettingsEditor>["set"] };

function useInputStyle() {
  const theme = useTheme();
  return [styles.input, { color: theme.text, backgroundColor: theme.surfaceAlt, borderColor: theme.border }];
}

function ConnectionCard() {
  const router = useRouter();
  const baseUrl = useConnection((s) => s.baseUrl);
  return (
    <Card title="Connection">
      <Row label="Server" value={baseUrl || "Not set"} mono />
      <PrimaryButton title="Connection & test" variant="secondary" onPress={() => {
        router.push("/connection");
      }} />
    </Card>
  );
}

function ProfileCard() {
  const router = useRouter();
  return (
    <Card title="Candidate profile">
      <Muted>Verified facts are the only source for résumés, cover letters and form answers.</Muted>
      <PrimaryButton title="Profile & verified facts ›" variant="secondary" onPress={() => {
        router.push("/profile");
      }} />
    </Card>
  );
}

function SafetyCard({ settings }: { settings: ServerSettings }) {
  return (
    <Card title="Safety (read-only)">
      <LockBanner />
      <Row label="Application mode" value={settings.application_mode} />
      <Row label="Continuous run" value={settings.continuous_run_enabled ? "Enabled" : "Disabled"} />
      <Row label="API auth" value={settings.auth_mode} />
      <Muted>These can only be changed in the server's .env. The kill switch is on the Dashboard.</Muted>
    </Card>
  );
}

function AutomationCard({ form, set, timezone }: FormProps & { timezone: string }) {
  const theme = useTheme();
  const input = useInputStyle();
  return (
    <Card title="Automation">
      <View style={styles.switchRow}>
        <View style={{ flex: 1 }}>
          <Text style={[styles.label, { color: theme.text }]}>Dry-runs enabled</Text>
          <Muted>Lets cycles fill approved applications' forms in memory. Never submits.</Muted>
        </View>
        <Switch value={form.automation_enabled} onValueChange={(value) => {
          set({ automation_enabled: value });
        }} />
      </View>
      <View style={styles.pair}>
        <View style={{ flex: 1, gap: 4 }}>
          <Text style={[styles.label, { color: theme.text }]}>Quiet from</Text>
          <TextInput value={form.quiet_hours_start} onChangeText={(v) => {
            set({ quiet_hours_start: v });
          }} placeholder="23:00" placeholderTextColor={theme.textFaint} style={input} />
        </View>
        <View style={{ flex: 1, gap: 4 }}>
          <Text style={[styles.label, { color: theme.text }]}>until</Text>
          <TextInput value={form.quiet_hours_end} onChangeText={(v) => {
            set({ quiet_hours_end: v });
          }} placeholder="07:00" placeholderTextColor={theme.textFaint} style={input} />
        </View>
      </View>
      <Muted>Times in {timezone}.</Muted>
    </Card>
  );
}

function LimitsCard({ form, set }: FormProps) {
  const theme = useTheme();
  const input = useInputStyle();
  return (
    <Card title="Limits">
      {NUMBER_FIELDS.map((field) => (
        <View key={field.key} style={styles.numberRow}>
          <View style={{ flex: 1 }}>
            <Text style={[styles.label, { color: theme.text }]}>{field.label}</Text>
            <Muted>{field.hint}</Muted>
          </View>
          <TextInput
            value={form[field.key]}
            onChangeText={(v) => {
              set({ [field.key]: v.replace(/[^0-9]/g, "") } as Partial<SettingsForm>);
            }}
            keyboardType="number-pad"
            style={[input, styles.numberInput]}
            accessibilityLabel={field.label}
          />
        </View>
      ))}
    </Card>
  );
}

function ListInputs({ form, set, fields }: FormProps & { fields: ListField[] }) {
  const theme = useTheme();
  const input = useInputStyle();
  return (
    <>
      {fields.map((field) => (
        <View key={field.key} style={{ gap: 4 }}>
          <Text style={[styles.label, { color: theme.text }]}>{field.label}</Text>
          <TextInput
            value={form[field.key]}
            onChangeText={(v) => {
              set({ [field.key]: v } as Partial<SettingsForm>);
            }}
            placeholder={field.hint ?? "comma-separated"}
            placeholderTextColor={theme.textFaint}
            autoCapitalize="none"
            autoCorrect={false}
            multiline
            style={[input, { minHeight: 44 }]}
          />
        </View>
      ))}
    </>
  );
}

function TargetingCard({ form, set }: FormProps) {
  return (
    <Card title="Targeting">
      <ListInputs form={form} set={set} fields={LIST_FIELDS} />
      <Muted>{parseList(form.target_keywords).length} keywords</Muted>
    </Card>
  );
}

function SourcesCard({ form, set, sources }: FormProps & { sources: ServerSettings["sources"] }) {
  return (
    <Card title="Job sources">
      <Muted>
        Public job boards to discover from (read-only, comma-separated slugs). Duplicates across boards are linked, not
        prepared twice.
      </Muted>
      <ListInputs form={form} set={set} fields={SOURCE_FIELDS} />
      <Muted>
        Saved: {sources.greenhouse_boards} Greenhouse · {sources.lever_companies} Lever · {sources.ashby_orgs} Ashby ·{" "}
        {sources.generic_feeds} other feeds
      </Muted>
    </Card>
  );
}

function AiCard({ settings }: { settings: ServerSettings }) {
  return (
    <Card title="AI">
      <Row label="Provider" value={settings.llm_provider} />
      <Row label="Fast model" value={settings.llm_fast_model} mono />
      <Row label="Quality model" value={settings.llm_quality_model} mono />
      <Row label="Browser check: Greenhouse" value={settings.greenhouse_browser_verify ? "On" : "Off"} />
      <Row label="Browser check: Lever" value={settings.lever_browser_verify ? "On" : "Off"} />
      <Row label="Browser check: Ashby" value={settings.ashby_browser_verify ? "On" : "Off"} />
    </Card>
  );
}

function BackupsCard({ backups }: { backups: Backups }) {
  return (
    <Card title={`Backups (${backups.items.length})`}>
      <Row label="Schedule" value={backups.interval_hours ? `every ${backups.interval_hours}h, keep ${backups.retention}` : "off"} />
      <Row label="Latest" value={formatDateTime(backups.latest_at)} />
      {backups.items.slice(0, 5).map((item) => (
        <Row key={item.name} label={formatDateTime(item.created_at)} value={formatBytes(item.size_bytes)} />
      ))}
      <Muted>Restore from the server (see docs/DEPLOY_VM.md). Backups never leave the server.</Muted>
    </Card>
  );
}

type Message = { tone: "info" | "danger"; text: string };

/** Loads settings + backups and keeps an editable form in sync with them. */
function useSettingsEditor() {
  const resource = useApiResource(loadSettings);
  const { data, setData } = resource;
  const [form, setForm] = useState<SettingsForm | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<Message>();

  useEffect(() => {
    if (data) setForm(settingsToForm(data.settings));
  }, [data]);

  const patch = useMemo(() => (data && form ? buildSettingsPatch(data.settings, form) : null), [data, form]);
  const dirty = !!patch && Object.keys(patch).length > 0;

  async function save() {
    if (!patch || !data) return;
    setSaving(true);
    setMessage(undefined);
    try {
      const settings = await api.updateSettings(patch);
      setData({ ...data, settings });
      setMessage({ tone: "info", text: "Saved on the server." });
    } catch (err) {
      setMessage({ tone: "danger", text: describeError(err) });
    } finally {
      setSaving(false);
    }
  }
  function set(changes: Partial<SettingsForm>) {
    setForm((current) => (current ? { ...current, ...changes } : current));
  }
  function discard() {
    if (data) setForm(settingsToForm(data.settings));
  }
  return { ...resource, form, set, dirty, saving, message, save, discard };
}

export default function SettingsScreen() {
  const theme = useTheme();
  const { data, loading, refreshing, error, refresh, reload, form, set, dirty, saving, message, save, discard } = useSettingsEditor();

  if (loading && !data) return <ScreenContainer><LoadingView /></ScreenContainer>;
  if (!data || !form) {
    return (
      <ScreenContainer>
        <ScrollView contentContainerStyle={styles.content}>
          <ConnectionCard />
          <ErrorView message={error ?? "No data."} onRetry={reload} />
        </ScrollView>
      </ScreenContainer>
    );
  }

  const { settings, backups } = data;
  return (
    <ScreenContainer>
      <ScrollView
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={theme.primary} />}
      >
        {error ? <Banner tone="danger" message={error} /> : null}
        <ConnectionCard />
        <ProfileCard />
        <SafetyCard settings={settings} />
        <AutomationCard form={form} set={set} timezone={settings.timezone} />
        <LimitsCard form={form} set={set} />
        <TargetingCard form={form} set={set} />
        <SourcesCard form={form} set={set} sources={settings.sources} />
        {message ? <Banner tone={message.tone} message={message.text} /> : null}
        <PrimaryButton title={dirty ? "Save changes" : "No changes"} disabled={!dirty} loading={saving} onPress={() => void save()} />
        {dirty ? <PrimaryButton title="Discard" variant="secondary" onPress={discard} /> : null}
        <AiCard settings={settings} />
        <BackupsCard backups={backups} />
      </ScrollView>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  content: { padding: 16, gap: 14 },
  label: { fontSize: 14, fontWeight: "700" },
  input: { borderWidth: 1, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 9, fontSize: 15 },
  switchRow: { flexDirection: "row", alignItems: "center", gap: 12 },
  pair: { flexDirection: "row", gap: 10 },
  numberRow: { flexDirection: "row", alignItems: "center", gap: 12 },
  numberInput: { width: 80, textAlign: "right" },
});
