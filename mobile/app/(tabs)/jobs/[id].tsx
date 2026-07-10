import { useLocalSearchParams, useNavigation } from "expo-router";
import { useEffect, useLayoutEffect, useMemo, useState } from "react";
import { Alert, Linking, ScrollView, StyleSheet, Text, View } from "react-native";

import { api, describeError, isConnectivityError } from "@/api/client";
import { Badge } from "@/components/Badge";
import { PrimaryButton } from "@/components/PrimaryButton";
import { ScreenContainer } from "@/components/ScreenContainer";
import { EmptyView, ErrorView, LoadingView } from "@/components/StatusViews";
import { mockJobs } from "@/mock/data";
import { useDataCacheStore } from "@/store/dataCache";
import { matchScoreColor, useTheme } from "@/theme";
import type { JobPosting } from "@/types";
import { formatDateTime } from "@/utils/format";

export default function JobDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const jobId = id;
  const theme = useTheme();
  const navigation = useNavigation();
  const cachedJobs = useDataCacheStore((s) => s.jobs);
  const applications = useDataCacheStore((s) => s.applications);
  const upsertApplication = useDataCacheStore((s) => s.upsertApplication);

  const cached = useMemo(() => cachedJobs.find((j) => j.id === jobId), [cachedJobs, jobId]);

  const [job, setJob] = useState<JobPosting | null | undefined>(cached);
  const [loading, setLoading] = useState(!cached);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useLayoutEffect(() => {
    navigation.setOptions({ title: job?.title ?? "Job Details" });
  }, [navigation, job?.title]);

  useEffect(() => {
    if (cached) {
      setJob(cached);
      setLoading(false);
      return;
    }
    // Deep link / cold start without a warm list cache — refetch.
    let cancelled = false;
    setLoading(true);
    api
      .getJobs()
      .then((jobs) => {
        if (cancelled) return;
        setJob(jobs.find((j) => j.id === jobId) ?? null);
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        if (isConnectivityError(err)) {
          setJob(mockJobs.find((j) => j.id === jobId) ?? null);
          setError(`${describeError(err)} (showing demo data)`);
        } else {
          setError(describeError(err));
        }
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [cached, jobId]);

  const existingApplication = applications.find((a) => a.job_posting_id === jobId);

  if (loading) {
    return (
      <ScreenContainer>
        <LoadingView label="Loading job…" />
      </ScreenContainer>
    );
  }

  if (!job) {
    return (
      <ScreenContainer>
        {error ? <ErrorView message={error} /> : <EmptyView message="Job not found." />}
      </ScreenContainer>
    );
  }

  const handleApply = async () => {
    setSubmitting(true);
    try {
      const created = await api.createApplication({ job_posting_id: job.id });
      upsertApplication(created);
      Alert.alert("Queued", `${job.title} at ${job.company} was queued for application.`);
    } catch (err) {
      Alert.alert("Couldn't queue application", describeError(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <ScreenContainer>
      <ScrollView contentContainerStyle={styles.content}>
        {error ? (
          <View style={[styles.noticeBanner, { backgroundColor: theme.warning + "22", borderColor: theme.warning }]}>
            <Text style={{ color: theme.text, fontSize: 12 }}>{error}</Text>
          </View>
        ) : null}

        <Text style={[styles.title, { color: theme.text }]}>{job.title}</Text>
        <Text style={[styles.company, { color: theme.textMuted }]}>{job.company}</Text>

        <View style={styles.badgeRow}>
          {job.match_score !== null ? (
            <Badge label={`Match ${job.match_score}`} color={matchScoreColor(theme, job.match_score)} />
          ) : null}
          <Badge label={job.source} color={theme.info} />
          {job.remote ? <Badge label="Remote" color={theme.success} /> : null}
        </View>

        <Text style={[styles.meta, { color: theme.textFaint }]}>
          {job.location ?? "Location unknown"} · discovered {formatDateTime(job.discovered_at)}
        </Text>

        <Text style={[styles.sectionTitle, { color: theme.text }]}>Description</Text>
        <Text style={[styles.description, { color: theme.textMuted }]}>{job.description}</Text>

        <View style={styles.linkRow}>
          <PrimaryButton
            title="View original posting"
            variant="secondary"
            onPress={() => Linking.openURL(job.url).catch(() => Alert.alert("Couldn't open link", job.url))}
          />
        </View>

        <View style={styles.applyRow}>
          {existingApplication ? (
            <Badge label={`Already ${existingApplication.status.replace("_", " ")}`} color={theme.info} />
          ) : (
            <PrimaryButton
              title="Apply / queue application"
              onPress={handleApply}
              loading={submitting}
            />
          )}
        </View>
      </ScrollView>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  content: {
    padding: 16,
    paddingBottom: 40,
    gap: 6,
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
    marginBottom: 8,
  },
  badgeRow: {
    flexDirection: "row",
    gap: 8,
    flexWrap: "wrap",
    marginBottom: 6,
  },
  meta: {
    fontSize: 12,
    marginBottom: 14,
  },
  sectionTitle: {
    fontSize: 15,
    fontWeight: "700",
    marginBottom: 6,
  },
  description: {
    fontSize: 14,
    lineHeight: 21,
  },
  linkRow: {
    marginTop: 20,
  },
  applyRow: {
    marginTop: 14,
  },
});
