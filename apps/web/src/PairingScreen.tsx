import { Alert, Button, PasswordInput, Stack, Text, Title } from "@mantine/core";
import { type FormEvent, useState } from "react";

interface PairingScreenProps {
  onPair: (code: string) => Promise<void>;
}

export function PairingScreen({ onPair }: PairingScreenProps) {
  const [code, setCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [failed, setFailed] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = code.trim();
    if (!value) return;
    setSubmitting(true);
    setFailed(false);
    try {
      await onPair(value);
    } catch {
      setFailed(true);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="pairing-page">
      <section className="pairing-panel" aria-label="Browser pairing">
        <div className="pairing-brand" aria-hidden="true">
          <Text className="parserium-wordmark">Parserium</Text>
          <Text className="machine-data">Local document operations</Text>
        </div>
        <form onSubmit={submit} className="pairing-form">
          <Stack gap="lg">
            <div>
              <Text className="section-kicker">Local access control</Text>
              <Title order={1}>Pair this browser</Title>
              <Text mt="xs" c="var(--parserium-text-muted)">
                Enter the temporary code printed in the local API container log.
              </Text>
            </div>
            {failed ? (
              <Alert role="alert" color="red" title="Pairing failed">
                The pairing code is invalid or expired.
              </Alert>
            ) : null}
            <PasswordInput
              label="Pairing code"
              description="The code remains in this browser session only."
              styles={{ description: { color: "var(--parserium-text-muted)" } }}
              value={code}
              onChange={(event) => setCode(event.currentTarget.value)}
              autoComplete="one-time-code"
              required
            />
            <Button
              className="pairing-action"
              type="submit"
              loading={submitting}
              disabled={!code.trim()}
            >
              Pair browser
            </Button>
          </Stack>
        </form>
      </section>
    </main>
  );
}
