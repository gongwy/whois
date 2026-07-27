package net.ripe.db.whois.update.autokey;

import com.google.common.base.Splitter;
import net.ripe.db.whois.common.domain.CIString;
import net.ripe.db.whois.common.rpsl.AttributeType;
import net.ripe.db.whois.common.rpsl.RpslObject;
import net.ripe.db.whois.common.rpsl.ValidationMessages;
import net.ripe.db.whois.update.autokey.dao.NicHandleRepository;
import net.ripe.db.whois.update.dao.CountryCodeRepository;
import net.ripe.db.whois.update.domain.NicHandle;
import net.ripe.db.whois.update.domain.NicHandleParseException;
import net.ripe.db.whois.update.domain.UpdateMessages;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static net.ripe.db.whois.common.domain.CIString.ciString;

@Component
public class NicHandleFactory extends AbstractAutoKeyFactory<NicHandle> {
    private static final Splitter CONFIG_SPLITTER = Splitter.on(',').trimResults().omitEmptyStrings();
    private static final Splitter ENTRY_SPLITTER = Splitter.on(':').trimResults().omitEmptyStrings();

    private final NicHandleRepository nicHandleRepository;
    private final CountryCodeRepository countryCodeRepository;
    private Map<CIString, String> suffixBySource = Collections.emptyMap();

    @Autowired
    public NicHandleFactory(final NicHandleRepository nicHandleRepository, final CountryCodeRepository countryCodeRepository) {
        super(nicHandleRepository);

        this.nicHandleRepository = nicHandleRepository;
        this.countryCodeRepository = countryCodeRepository;
    }

    @Override
    public AttributeType getAttributeType() {
        return AttributeType.NIC_HDL;
    }

    @Override
    public NicHandle generate(final String keyPlaceHolder, final RpslObject object) {
        return generateForName(keyPlaceHolder, object.getTypeAttribute().getCleanValue().toString());
    }

    @Override
    public NicHandle claim(final String key) throws ClaimException {
        final NicHandle nicHandle = parseNicHandle(key);
        if (!nicHandleRepository.claimSpecified(nicHandle)) {
            throw new ClaimException(UpdateMessages.nicHandleNotAvailable(nicHandle.toString()));
        }

        return nicHandle;
    }

    public boolean isAvailable(final String key) throws ClaimException {
        final NicHandle nicHandle = parseNicHandle(key);
        return nicHandleRepository.isAvailable(nicHandle);
    }

    @Value("${user.handle.suffix.config:}")
    protected void setUserHandleSuffixConfig(final String config) {
        final Map<CIString, String> configuredSuffixBySource = new HashMap<>();
        for (final String entry : CONFIG_SPLITTER.split(config)) {
            final List<String> parts = ENTRY_SPLITTER.splitToList(entry);
            if (parts.size() != 2) {
                throw new IllegalArgumentException("Invalid user.handle.suffix.config entry: " + entry);
            }

            configuredSuffixBySource.put(ciString(parts.get(0)), trimLeadingDash(parts.get(1)));
        }

        suffixBySource = configuredSuffixBySource;
    }

    @Override
    protected String getGeneratedSuffix() {
        return getConfiguredSuffixForSource();
    }

    private NicHandle parseNicHandle(final String key) throws ClaimException {
        try {
            return NicHandle.parse(key, getSource(), countryCodeRepository.getCountryCodes(), getConfiguredSuffixesForSource());
        } catch (NicHandleParseException e) {
            throw new ClaimException(ValidationMessages.syntaxError(key));
        }
    }

    private Set<CIString> getConfiguredSuffixesForSource() {
        final String configuredSuffix = suffixBySource.get(getSource());
        return configuredSuffix == null ? Collections.<CIString>emptySet() : Collections.singleton(ciString(configuredSuffix));
    }

    private String getConfiguredSuffixForSource() {
        final String configuredSuffix = suffixBySource.get(getSource());
        return configuredSuffix == null ? getSource().toString() : configuredSuffix;
    }

    private String trimLeadingDash(final String suffix) {
        return suffix.startsWith("-") ? suffix.substring(1) : suffix;
    }
}
