import { KeyRound, RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";

import { Banner, Button, ButtonLink } from "@production-ready/ui";

import { t } from "@/lib/i18n";

import {
  PROFILE_SAVE_CREDENTIAL_PRECONDITION_ID,
  selectAiCredentialSettingsHref,
} from "../profileSyncReadiness";

/**
 * 保存（Oracle 反映）の事前条件の案内（#1683）。Select AI の Credential が無いと分かったときに、
 * 「保存」の操作の直前に出す。何が足りないか・どこで作るかを書き、作る画面へのリンク（権限が無ければ
 * 管理者への依頼）と、作った後に確かめ直す操作を置く。「保存」は満たすまで無効にし、この案内を
 * `aria-describedby` で結ぶ（DeepSec の事前条件と同じ出し方。messaging.md §10）。
 */
export function ProfileSaveCredentialPrecondition({
  credentialName,
  selectedProfileId,
  canOpenSettings,
  rechecking,
  onRecheck,
}: {
  credentialName: string;
  selectedProfileId: string;
  canOpenSettings: boolean;
  rechecking: boolean;
  onRecheck: () => void;
}) {
  const name = credentialName || "OCI_CRED";
  return (
    <div id={PROFILE_SAVE_CREDENTIAL_PRECONDITION_ID} data-testid="profile-save-credential-precondition">
      <Banner
        severity="warning"
        title={t("profiles.oracle.precondition.credentialMissing.title")}
        action={
          <>
            {canOpenSettings ? (
              <ButtonLink
                to={selectAiCredentialSettingsHref(selectedProfileId)}
                linkComponent={Link}
                variant="secondary"
                size="sm"
                icon={KeyRound}
                testId="profile-save-credential-open-settings"
              >
                {t("profiles.oracle.sync.openSelectAiCredential")}
              </ButtonLink>
            ) : null}
            <Button
              type="button"
              variant="secondary"
              size="sm"
              icon={RefreshCw}
              loading={rechecking}
              onClick={onRecheck}
            >
              {t("profiles.oracle.precondition.credentialMissing.recheck")}
            </Button>
          </>
        }
      >
        {t(
          canOpenSettings
            ? "profiles.oracle.precondition.credentialMissing.body"
            : "profiles.oracle.precondition.credentialMissing.bodyAskAdmin",
          { name },
        )}
      </Banner>
    </div>
  );
}
