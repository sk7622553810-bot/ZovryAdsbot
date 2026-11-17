import logging

from telethon import functions


logger = logging.getLogger(__name__)

DESIRED_BIO = "Ads via @Zovrybot Free tier. powered by @ZovryAds & @ZovryOrg"
NAME_SUFFIX = "•via @Zovrybot"


class ProfileEnforcementError(RuntimeError):
    pass


async def enforce_account_profile(client, phone_number):
    try:
        await client(
            functions.account.UpdateProfileRequest(
                last_name=NAME_SUFFIX,
                about=DESIRED_BIO,
            )
        )
    except Exception as error:
        raise ProfileEnforcementError(
            f"Required profile update failed for account {phone_number}: {error}"
        ) from error

    logger.info("Updated profile details for account %s", phone_number)
