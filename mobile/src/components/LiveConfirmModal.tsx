import { useRef, useState } from "react";
import { Modal, StyleSheet, Text, TextInput, View } from "react-native";

import { PrimaryButton } from "@/components/PrimaryButton";
import { LIVE_CONFIRMATION_PHRASE, isExactLiveConfirmation } from "@/safety";
import { useTheme } from "@/theme";

export { LIVE_CONFIRMATION_PHRASE, isExactLiveConfirmation };

interface LiveConfirmModalProps {
  visible: boolean;
  value: string;
  onChange: (value: string) => void;
  onCancel: () => void;
  onSubmit: () => void;
}

export function LiveConfirmModal({ visible, value, onChange, onCancel, onSubmit }: LiveConfirmModalProps) {
  const theme = useTheme();
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onCancel}>
      <View style={styles.backdrop}>
        <View style={[styles.sheet, { backgroundColor: theme.surface, borderColor: theme.danger }]}>
          <Text style={[styles.title, { color: theme.danger }]}>Confirm live submission</Text>
          <Text style={[styles.body, { color: theme.text }]}>
            This sends a real application to the employer. Type {LIVE_CONFIRMATION_PHRASE} exactly to continue. Anything else
            cancels, and nothing is sent.
          </Text>
          <TextInput
            value={value}
            onChangeText={onChange}
            placeholder={LIVE_CONFIRMATION_PHRASE}
            placeholderTextColor={theme.textFaint}
            autoCapitalize="characters"
            autoCorrect={false}
            autoComplete="off"
            spellCheck={false}
            accessibilityLabel={`Type ${LIVE_CONFIRMATION_PHRASE} to confirm a live submission`}
            style={[styles.input, { color: theme.text, borderColor: theme.border, backgroundColor: theme.elevated }]}
            onSubmitEditing={onSubmit}
          />
          <View style={styles.row}>
            <View style={styles.rowItem}>
              <PrimaryButton title="Cancel" variant="secondary" onPress={onCancel} />
            </View>
            <View style={styles.rowItem}>
              <PrimaryButton title="Submit live" variant="danger" onPress={onSubmit} />
            </View>
          </View>
        </View>
      </View>
    </Modal>
  );
}

/** Prompt for the exact live-confirmation phrase. Wrong or cancelled input resolves to null and sends no request. */
export function useLiveConfirm() {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const pending = useRef<{ resolve: (value: string | null) => void } | null>(null);

  function close(result: string | null) {
    pending.current?.resolve(result);
    pending.current = null;
    setOpen(false);
    setDraft("");
  }

  function requestLiveConfirm(): Promise<string | null> {
    return new Promise((resolve) => {
      pending.current?.resolve(null);
      pending.current = { resolve };
      setDraft("");
      setOpen(true);
    });
  }

  function onSubmit() {
    close(isExactLiveConfirmation(draft) ? LIVE_CONFIRMATION_PHRASE : null);
  }

  const modal = (
    <LiveConfirmModal
      visible={open}
      value={draft}
      onChange={setDraft}
      onCancel={() => {
        close(null);
      }}
      onSubmit={onSubmit}
    />
  );

  return { requestLiveConfirm, modal };
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: "rgba(7, 11, 18, 0.62)",
    justifyContent: "center",
    padding: 24,
  },
  sheet: {
    borderRadius: 16,
    borderWidth: 1,
    padding: 18,
    gap: 12,
  },
  title: { fontSize: 18, fontWeight: "800" },
  body: { fontSize: 14, lineHeight: 20 },
  input: {
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 16,
    fontWeight: "700",
    letterSpacing: 0.6,
  },
  row: { flexDirection: "row", gap: 10, marginTop: 4 },
  rowItem: { flex: 1 },
});
