import { useState, type Dispatch, type SetStateAction } from "react";
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Switch, Text, TextInput, View } from "react-native";

import { api, describeError } from "@/api/client";
import type { Profile, ProfileFact, ProfileImport, ProfileImportResult } from "@/api/types";
import { Badge } from "@/components/Badge";
import { Banner } from "@/components/Banner";
import { Card, Muted, Row } from "@/components/Card";
import { Chip } from "@/components/Chip";
import { PrimaryButton } from "@/components/PrimaryButton";
import { ScreenContainer } from "@/components/ScreenContainer";
import { ErrorView, LoadingView } from "@/components/StatusViews";
import { useApiResource } from "@/hooks/useApiResource";
import { confirmAsync } from "@/lib/confirm";
import { applyEdits, editableFields, factSubtitle, factTitle, groupFacts, readinessCopy, type EditableField } from "@/profile";
import { useTheme } from "@/theme";

type Refresh = () => Promise<void>;
type ImportFormat = ProfileImport["format"];

const FORMATS: { value: ImportFormat; label: string }[] = [
  { value: "yaml", label: "YAML" },
  { value: "json", label: "JSON" },
  { value: "resume_text", label: "Résumé text" },
];

function useInputStyle() {
  const theme = useTheme();
  return [styles.input, { color: theme.text, backgroundColor: theme.surfaceAlt, borderColor: theme.border }];
}

function ReadinessCard({ profile }: { profile: Profile }) {
  const theme = useTheme();
  return (
    <Card title="Verified profile" right={<Badge label={profile.ready ? "Ready" : "Not ready"} color={profile.ready ? theme.success : theme.warning} />}>
      <Row label="Facts" value={String(profile.total)} />
      <Row label="Verified" value={String(profile.verified)} valueColor={theme.success} />
      <Row label="Unverified" value={String(profile.unverified)} valueColor={profile.unverified ? theme.warning : theme.text} />
      <Muted>{readinessCopy(profile.ready, profile.missing)}</Muted>
      <Muted>Only verified facts are ever used in résumés, cover letters or form answers. Imported facts start unverified.</Muted>
    </Card>
  );
}

function ImportResultView({ result }: { result: ProfileImportResult }) {
  return (
    <View style={{ gap: 4 }}>
      <Muted>
        Imported: {result.created} new, {result.updated} updated, {result.unchanged} unchanged · {result.unverified} need your confirmation.
      </Muted>
      {result.warnings.slice(0, 8).map((warning) => (
        <Muted key={warning}>• {warning}</Muted>
      ))}
    </View>
  );
}

function useProfileImport(refresh: Refresh) {
  const [format, setFormat] = useState<ImportFormat>("yaml");
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ProfileImportResult>();
  const [error, setError] = useState<string>();

  async function submit() {
    setBusy(true);
    setError(undefined);
    try {
      setResult(await api.importProfile({ format, content, filename: "phone" }));
      setContent("");
      await refresh();
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }
  return { format, setFormat, content, setContent, busy, result, error, submit };
}

function FormatChips({ format, setFormat }: { format: ImportFormat; setFormat: Dispatch<SetStateAction<ImportFormat>> }) {
  return (
    <View style={styles.chips}>
      {FORMATS.map((item) => (
        <Chip key={item.value} label={item.label} selected={format === item.value} onPress={() => {
          setFormat(item.value);
        }} />
      ))}
    </View>
  );
}

function ImportCard({ refresh }: { refresh: Refresh }) {
  const theme = useTheme();
  const input = useInputStyle();
  const { format, setFormat, content, setContent, busy, result, error, submit } = useProfileImport(refresh);

  return (
    <Card title="Import">
      <FormatChips format={format} setFormat={setFormat} />
      <TextInput value={content} onChangeText={setContent} multiline placeholder="Paste profile YAML/JSON or plain résumé text" placeholderTextColor={theme.textFaint} style={[input, styles.multiline]} />
      {error ? <Banner tone="danger" message={error} /> : null}
      {result ? <ImportResultView result={result} /> : null}
      <PrimaryButton title="Import as drafts" variant="secondary" disabled={!content.trim()} loading={busy} onPress={() => void submit()} />
    </Card>
  );
}

type Edits = Map<string, string>;

function FieldInput({ item, edits, setEdits }: { item: EditableField; edits: Edits; setEdits: Dispatch<SetStateAction<Edits>> }) {
  const theme = useTheme();
  const input = useInputStyle();
  const value = edits.get(item.name) ?? "";

  function onChange(name: string, next: string) {
    setEdits((current) => new Map(current).set(name, next));
  }

  return (
    <View style={{ gap: 4 }}>
      <Text style={[styles.fieldName, { color: theme.textMuted }]}>{item.name.replace(/_/g, " ")}{item.kind === "list" ? " (comma-separated)" : ""}</Text>
      {item.kind === "boolean" ? (
        <Switch value={value === "true"} onValueChange={(next) => {
          onChange(item.name, String(next));
        }} />
      ) : (
        <TextInput value={value} onChangeText={(next) => {
          onChange(item.name, next);
        }} multiline={item.name === "text" || item.name === "summary"} style={input} placeholderTextColor={theme.textFaint} />
      )}
    </View>
  );
}

function FactEditor({ fact, close, refresh }: { fact: ProfileFact; close: () => void; refresh: Refresh }) {
  const fields = editableFields(fact.data);
  const [edits, setEdits] = useState<Edits>(() => new Map(fields.map((item) => [item.name, item.value])));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  async function save(verify: boolean) {
    setBusy(true);
    setError(undefined);
    try {
      await api.editFact(fact.id, applyEdits(fact.data, edits), verify);
      close();
      await refresh();
    } catch (err) {
      setError(describeError(err));
      setBusy(false);
    }
  }

  return (
    <View style={styles.editor}>
      {fields.map((item) => (
        <FieldInput key={item.name} item={item} edits={edits} setEdits={setEdits} />
      ))}
      {error ? <Banner tone="danger" message={error} /> : null}
      <Muted>Saving without verifying marks the fact unverified until you confirm it.</Muted>
      <View style={styles.buttons}>
        <PrimaryButton title="Save" variant="secondary" loading={busy} onPress={() => void save(false)} />
        <PrimaryButton title="Save and verify" loading={busy} onPress={() => void save(true)} />
        <PrimaryButton title="Cancel" variant="secondary" disabled={busy} onPress={close} />
      </View>
    </View>
  );
}

function useFactActions(fact: ProfileFact, refresh: Refresh) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(undefined);
    try {
      await action();
      await refresh();
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  async function toggle() {
    if (!fact.verified) {
      const ok = await confirmAsync("Verify this fact?", `"${factTitle(fact)}"\n\nConfirm it is exactly true. Verified facts can appear in résumés, cover letters and form answers.`, "Verify");
      if (!ok) return;
    }
    await run(() => api.verifyFact(fact.id, !fact.verified));
  }

  async function remove() {
    if (await confirmAsync("Remove this fact?", factTitle(fact), "Remove", true)) await run(() => api.removeFact(fact.id));
  }
  return { busy, error, toggle, remove };
}

function SmallAction({ title, color, onPress, disabled = false, loading = false }: { title: string; color: string; onPress: () => void; disabled?: boolean; loading?: boolean }) {
  const inactive = disabled || loading;
  return (
    <Pressable accessibilityRole="button" onPress={onPress} disabled={inactive} style={({ pressed }) => [styles.small, { borderColor: color, opacity: pressed || inactive ? 0.6 : 1 }]}>
      {loading ? <ActivityIndicator color={color} size="small" /> : <Text style={[styles.smallText, { color }]}>{title}</Text>}
    </Pressable>
  );
}

function FactRow({ fact, refresh }: { fact: ProfileFact; refresh: Refresh }) {
  const theme = useTheme();
  const [editing, setEditing] = useState(false);
  const { busy, error, toggle, remove } = useFactActions(fact, refresh);
  const subtitle = factSubtitle(fact);

  function close() {
    setEditing(false);
  }

  return (
    <View style={[styles.fact, { borderColor: theme.border }]}>
      <View style={styles.factHeader}>
        <Text style={[styles.factTitle, { color: theme.text }]}>{factTitle(fact)}</Text>
        <Badge label={fact.verified ? "Verified" : "Unverified"} color={fact.verified ? theme.success : theme.warning} />
      </View>
      {subtitle ? <Muted>{subtitle}</Muted> : null}
      <Text style={[styles.source, { color: theme.textFaint }]}>{fact.source} · {fact.provenance || fact.key}</Text>
      {error ? <Banner tone="danger" message={error} /> : null}
      {editing ? <FactEditor fact={fact} close={close} refresh={refresh} /> : (
        <View style={styles.factActions}>
          <SmallAction title={fact.verified ? "Unverify" : "Verify"} color={fact.verified ? theme.textMuted : theme.success} loading={busy} onPress={() => void toggle()} />
          <SmallAction title="Edit" color={theme.primary} disabled={busy} onPress={() => {
            setEditing(true);
          }} />
          <SmallAction title="Remove" color={theme.danger} disabled={busy} onPress={() => void remove()} />
        </View>
      )}
    </View>
  );
}

function FactGroups({ profile, refresh }: { profile: Profile; refresh: Refresh }) {
  const theme = useTheme();
  if (!profile.facts.length) return <Muted>No facts yet. Import a profile file or résumé text above, or run `./hunterx profile import` on the server.</Muted>;
  return (
    <>
      {groupFacts(profile.facts).map((group) => (
        <Card key={group.category} title={`${group.label} (${group.facts.length})`} right={group.unverified ? <Badge label={`${group.unverified} unverified`} color={theme.warning} /> : null}>
          {group.facts.map((fact) => (
            <FactRow key={fact.id} fact={fact} refresh={refresh} />
          ))}
        </Card>
      ))}
    </>
  );
}

export default function ProfileScreen() {
  const theme = useTheme();
  const { data: profile, loading, refreshing, error, refresh, reload } = useApiResource(() => api.profile(), []);

  if (loading && !profile) return <ScreenContainer><LoadingView /></ScreenContainer>;
  if (!profile) return <ScreenContainer><ErrorView message={error ?? "Profile unavailable."} onRetry={reload} /></ScreenContainer>;

  return (
    <ScreenContainer>
      <ScrollView
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={theme.primary} />}
      >
        {error ? <Banner tone="danger" message={error} /> : null}
        <ReadinessCard profile={profile} />
        <ImportCard refresh={refresh} />
        <FactGroups profile={profile} refresh={refresh} />
      </ScrollView>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  content: { padding: 16, gap: 14 },
  chips: { flexDirection: "row", gap: 8, flexWrap: "wrap" },
  input: { borderWidth: 1, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 9, fontSize: 15 },
  multiline: { minHeight: 120, textAlignVertical: "top" },
  fact: { borderTopWidth: StyleSheet.hairlineWidth, paddingTop: 10, gap: 4 },
  factHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-start", gap: 8 },
  factTitle: { fontSize: 14, fontWeight: "600", flexShrink: 1, lineHeight: 19 },
  source: { fontSize: 11 },
  factActions: { flexDirection: "row", gap: 8, flexWrap: "wrap", marginTop: 2 },
  small: { borderWidth: 1, borderRadius: 999, paddingHorizontal: 12, paddingVertical: 4, minWidth: 64, alignItems: "center" },
  smallText: { fontSize: 12, fontWeight: "700" },
  editor: { gap: 8, marginTop: 4 },
  fieldName: { fontSize: 12, fontWeight: "700", textTransform: "capitalize" },
  buttons: { gap: 8 },
});
