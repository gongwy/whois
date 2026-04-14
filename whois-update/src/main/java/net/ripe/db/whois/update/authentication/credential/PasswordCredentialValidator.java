package net.ripe.db.whois.update.authentication.credential;

import net.ripe.db.whois.common.Message;
import net.ripe.db.whois.common.Messages;
import net.ripe.db.whois.common.rpsl.PasswordHelper;
import net.ripe.db.whois.update.domain.PasswordCredential;
import net.ripe.db.whois.update.domain.PreparedUpdate;
import net.ripe.db.whois.update.domain.UpdateContext;
import net.ripe.db.whois.update.log.LoggerContext;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.Collection;
import java.nio.charset.StandardCharsets;


@Component
class PasswordCredentialValidator implements CredentialValidator<PasswordCredential, PasswordCredential> {
    private final LoggerContext loggerContext;
    private static final Logger LOGGER = LoggerFactory.getLogger(PasswordCredentialValidator.class);

    @Autowired
    PasswordCredentialValidator(LoggerContext loggerContext) {
        this.loggerContext = loggerContext;
    }

    @Override
    public Class<PasswordCredential> getSupportedCredentials() {
        return PasswordCredential.class;
    }

    @Override
    public Class<PasswordCredential> getSupportedOfferedCredentialType() {
        return PasswordCredential.class;
    }

    @Override
    public boolean hasValidCredential(final PreparedUpdate update,
                                      final UpdateContext updateContext,
                                      final Collection<PasswordCredential> offeredCredentials,
                                      final PasswordCredential knownCredential) {
        LOGGER.info("[GWY LOG] entered into PasswordCredentialValidator");

        for (final PasswordCredential offeredCredential : offeredCredentials) {
            LOGGER.info("[GWY LOG] offeredCredential is not null");
            try {
                String offeredPassword = offeredCredential.getPassword();
                String knownPassword = knownCredential.getPassword();
                byte[] offeredBytes = offeredPassword.getBytes(StandardCharsets.UTF_8);
                String offeredHex = org.apache.commons.codec.binary.Hex.encodeHexString(offeredBytes);

                LOGGER.info("[GWY DEBUG] Offered Password Plain: [{}]", offeredPassword);
                LOGGER.info("[GWY DEBUG] Offered Password Hex: {}", offeredHex);
                LOGGER.info("[GWY DEBUG] Known Hash from DB: [{}]", knownPassword);
                
                if (PasswordHelper.authenticateMd5Passwords(knownPassword, offeredPassword)) {
                    loggerContext.logString(
                            update.getUpdate(),
                            getClass().getCanonicalName(),
                            String.format("Validated %s against known encrypted password: %s)", update.getFormattedKey(), knownPassword));
                    LOGGER.info("[GWY LOG] valid password");

                    return true;
                }
            } catch (IllegalArgumentException e) {
                LOGGER.info("[GWY LOG] error occur");

                updateContext.addGlobalMessage(new Message(Messages.Type.WARNING, e.getMessage()));
            }
        }
        
        LOGGER.info("[GWY LOG] return false");
        return false;
    }
}
