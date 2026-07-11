import { useLocalSearchParams, useNavigation } from "expo-router";
import { useEffect, useLayoutEffect, useMemo, useState } from "react";
import {
  Alert,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import { api, describeError, isConnectivityError } from "@/api/client";
import { Badge } from "@/components/Badge";
import { PrimaryButton } from "@/components/PrimaryButton";
import { ScreenContainer } from "@/components/ScreenContainer";
import { EmptyView, ErrorView, LoadingView } from "@/components/StatusViews";
import { mockApplications } from "@/mock/data";
import { useDataCacheStore } from "@/store/dataCache";
import { statusColor, useTheme } from "@/theme";
import { APPLICATION_STATUSES, type ApplicationRecord, type ApplicationStatus } from "@/types";
import { formatDateTime, titleCase } from "@/utils/format";

export default function ApplicationDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const applicationId = id;
  const theme = useTheme();
  const navigation = useNavigation();
  const cachedApplications = useDataCacheStore((s) => s.applications);
  const upsertApplication = useDataCacheStore((s) => s.upsertApplication);

  const cached = useMemo(
    () => cachedApplications.find((a) => a.id === applicationId),
    [cachedApplications, applicationId]
  );

  const [application, setApplication] = useState<ApplicationRecord | null | undefined>(cached);
  const [loading, setLoading] = useState(!cached);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [selectedStatus, setSelectedStatus] = useState<ApplicationStatus | null>(cached?.status ?? null);
  const [notesDraft, setNotesDraft] = useState(cached?.notes ?? "");
  const [saving, setSaving] = useState(false);

  useLayoutEffect(() => {
    navigation.setOptions({ title: application?.job?.title ?? "Application" });
  }, [navigation, application?.job?.title]);

  useEffect(() => {
    if (cached) {
      setApplication(cached);
      setSelectedStatus(cached.status);
      setNotesDraft(cached.notes ?? "");
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    api
      .getApplications()
      .then((apps) => {
        if (cancelled) return;
        const found = apps.find((a) => a.id === applicationId) ?? null;
        setApplication(found);
        setSelectedStatus(found?.status ?? null);
        setNotesDraft(found?.notes ?? "");
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        if (isConnectivityError(err)) {
          const found = mockApplications.find((a) => a.id === applicationId) ?? null;
          setApplication(found);
          setSelectedStatus(found?.status ?? null);
          setNotesDraft(found?.notes ?? "");
          setLoadError(`${describeError(err)} (showing demo data)`);
        } else {
          setLoadError(describeError(err));
        }
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [cached, applicationId]);

  if (loading) {
    return (
      <ScreenContainer>
        <LoadingView label="Loading application…" />
      </ScreenContainer>
    );
  }

  if (!application) {
    return (
      <ScreenContainer>
        {loadError ? <ErrorView message={loadError} /> : <EmptyView message="Application not found." />}
      </ScreenContainer>
    );
  }

  const dirty = selectedStatus !== application.status || (notesDraft ?? "") !== (application.notes ?? "");

  const handleSave = async () => {
    if (!selectedStatus) return;
    setSaving(true);
    try {
      const updated = await api.updateApplication(application.id, {
        status: selectedStatus,
        notes: notesDraft,
      });
      const merged: ApplicationRecord = { ...application, ...updated };
      setApplication(merged);
      upsertApplication(merged);
      Alert.alert("Saved", "Application updated.");
    } catch (err) {
      Alert.alert("Couldn't save changes", describeError(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <ScreenContainer>
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        keyboardVerticalOffset={80}
      >
        <ScrollView contentContainerStyle={styles.content}>
          {loadError ? (
            <View style={[styles.noticeBanner, { backgroundColor: theme.warning + "22", borderColor: theme.warning }]}>
              <Text style={{ color: theme.text, fontSize: 12 }}>{loadError}</Text>
            </View>
          ) : null}

          <Text style={[styles.title, { color: theme.text }]}>{application.job?.title ?? `Job #${application.job_posting_id}`}</Text>
          <Text style={[styles.company, { color: theme.textMuted }]}>{application.job?.company ?? "Unknown company"}</Text>

          <View style={styles.metaGrid}>
            <MetaRow theme={theme} label="Channel" value={application.channel} />
            <MetaRow theme={theme} label="Submitted" value={formatDateTime(application.submitted_at)} />
            <MetaRow theme={theme} label="Last status change" value={formatDateTime(application.last_status_change)} />
          </View>

          <Text style={[styles.sectionTitle, { color: theme.text }]}>Cover letter preview</Text>
          <View style={[styles.coverLetterBox, { backgroundColor: theme.surface, borderColor: theme.border }]}>
            <Text style={[styles.coverLetterText, { color: theme.textMuted }]}>
              {application.cover_letter_text ?? "No cover letter generated yet."}
            </Text>
          </View>

          <Text style={[styles.sectionTitle, { color: theme.text }]}>Status</Text>
          <View style={styles.statusRow}>
            {APPLICATION_STATUSES.map((status) => {
              const active = selectedStatus === status;
              const color = statusColor(theme, status);
              return (
                <Pressable
                  key={status}
                  onPress={() => setSelectedStatus(status)}
                  style={[
                    styles.statusChip,
                    { backgroundColor: active ? color + "26" : theme.surface, borderColor: active ? color : theme.border },
                  ]}
                >
                  <Text style={{ color: active ? color : theme.textMuted, fontSize: 12, fontWeight: "700" }}>
                    {titleCase(status)}
                  </Text>
                </Pressable>
              );
            })}
          </View>

          <Text style={[styles.sectionTitle, { color: theme.text }]}>Notes</Text>
          <TextInput
            value={notesDraft ?? ""}
            onChangeText={setNotesDraft}
            placeholder="Add a note…"
            placeholderTextColor={theme.textFaint}
            multiline
            style={[
              styles.notesInput,
              { backgroundColor: theme.surface, borderColor: theme.border, color: theme.text },
            ]}
          />

          <View style={styles.saveRow}>
            <PrimaryButton title="Save changes" onPress={handleSave} loading={saving} disabled={!dirty} />
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </ScreenContainer>
  );
}

function MetaRow({ theme, label, value }: { theme: ReturnType<typeof useTheme>; label: string; value: string }) {
  return (
    <View style={styles.metaRow}>
      <Text style={[styles.metaLabel, { color: theme.textFaint }]}>{label}</Text>
      <Text style={[styles.metaValue, { color: theme.text }]}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  content: {
    padding: 16,
    paddingBottom: 60,
  },
  noticeBanner: {
    borderWidth: 1,
    borderRadius: 8,
    padding: 10,
    marginBottom: 8,
  },
  title: {
    fontSize: 22,
    fontWeight: "800",
  },
  company: {
    fontSize: 16,
    fontWeight: "600",
    marginBottom: 12,
  },
  metaGrid: {
    gap: 6,
    marginBottom: 8,
  },
  metaRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingVertical: 4,
  },
  metaLabel: {
    fontSize: 13,
  },
  metaValue: {
    fontSize: 13,
    fontWeight: "600",
  },
  sectionTitle: {
    fontSize: 15,
    fontWeight: "700",
    marginTop: 18,
    marginBottom: 8,
  },
  coverLetterBox: {
    borderWidth: 1,
    borderRadius: 12,
    padding: 14,
  },
  coverLetterText: {
    fontSize: 13,
    lineHeight: 20,
  },
  statusRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
  },
  statusChip: {
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 12,
    paddingVertical: 6,
  },
  notesInput: {
    borderWidth: 1,
    borderRadius: 12,
    padding: 12,
    minHeight: 90,
    fontSize: 14,
    textAlignVertical: "top",
  },
  saveRow: {
    marginTop: 20,
  },
});
