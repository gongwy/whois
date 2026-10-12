package net.ripe.db.whois.common.dao.jdbc;

import com.google.common.collect.Iterables;
import com.google.common.collect.Lists;
import com.google.common.collect.Maps;
import com.google.common.collect.Sets;
import net.ripe.db.whois.common.aspects.RetryFor;
import net.ripe.db.whois.common.dao.RpslObjectDao;
import net.ripe.db.whois.common.dao.RpslObjectInfo;
import net.ripe.db.whois.common.dao.jdbc.domain.RpslObjectInfoResultSetExtractor;
import net.ripe.db.whois.common.dao.jdbc.domain.RpslObjectRowMapper;
import net.ripe.db.whois.common.dao.jdbc.domain.ObjectTypeIds;
import net.ripe.db.whois.common.dao.jdbc.index.IndexStrategies;
import net.ripe.db.whois.common.dao.jdbc.index.IndexStrategy;
import net.ripe.db.whois.common.domain.CIString;
import net.ripe.db.whois.common.domain.Identifiable;
import net.ripe.db.whois.common.rpsl.AttributeType;
import net.ripe.db.whois.common.rpsl.ObjectTemplate;
import net.ripe.db.whois.common.rpsl.ObjectType;
import net.ripe.db.whois.common.rpsl.RpslAttribute;
import net.ripe.db.whois.common.rpsl.RpslObject;
import net.ripe.db.whois.common.source.IllegalSourceException;
import net.ripe.db.whois.common.source.Source;
import net.ripe.db.whois.common.source.SourceContext;
import org.apache.commons.lang.Validate;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.context.annotation.Primary;
import org.springframework.dao.DataAccessException;
import org.springframework.dao.EmptyResultDataAccessException;
import org.springframework.dao.IncorrectResultSizeDataAccessException;
import org.springframework.dao.RecoverableDataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.ResultSetExtractor;
import org.springframework.jdbc.core.PreparedStatementSetter;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Repository;


import javax.annotation.CheckForNull;
import javax.annotation.Nullable;
import javax.sql.DataSource;
import java.sql.PreparedStatement;
import java.sql.SQLException;
import java.sql.ResultSet;
import java.text.MessageFormat;
import java.util.stream.Collectors;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.HashMap;
import java.util.Locale;

@Repository
@Primary
@RetryFor(RecoverableDataAccessException.class)
public class JdbcRpslObjectDao implements RpslObjectDao {
    private static final Logger LOGGER = LoggerFactory.getLogger(JdbcRpslObjectDao.class);

    private static final List<AttributeType> RELATED_TO_ATTRIBUTES = Lists.newArrayList(AttributeType.ADMIN_C, AttributeType.AUTHOR, AttributeType.ORG, AttributeType.PING_HDL, AttributeType.TECH_C, AttributeType.ZONE_C);

    private final JdbcTemplate jdbcTemplate;
    private final SourceContext sourceContext;

    @Autowired
    public JdbcRpslObjectDao(@Qualifier("sourceAwareDataSource") final DataSource dataSource, final SourceContext sourceContext) {
        this.jdbcTemplate = new JdbcTemplate(dataSource);
        this.sourceContext = sourceContext;
    }

    @Override
    public void load(final List<Identifiable> proxy, final List<RpslObject> result) {
        final Map<Integer, RpslObject> loadedObjects = Maps.newHashMapWithExpectedSize(proxy.size());

        Set<Integer> differences = loadObjects(proxy, loadedObjects);
        if (!differences.isEmpty()) {
            final Source originalSource = sourceContext.getCurrentSource();
            LOGGER.info("Objects in source {} not found for ids: {}", originalSource, differences);

            if (originalSource.getType().equals(Source.Type.SLAVE)) {
                final Source masterSource = Source.master(originalSource.getName());
                try {
                    sourceContext.setCurrent(masterSource);
                    differences = loadObjects(proxy, loadedObjects);
                    if (!differences.isEmpty()) {
                        LOGGER.info("Objects in source {} not found for ids: {}", masterSource, differences);
                    }
                } catch (IllegalSourceException e) {
                    LOGGER.debug("Source not configured: {}", masterSource, e);
                } finally {
                    sourceContext.setCurrent(originalSource);
                }
            }
        }

        final List<RpslObject> rpslObjects = Lists.newArrayList(loadedObjects.values());
        Collections.sort(rpslObjects, new Comparator<RpslObject>() {
            final List<Integer> requestedIds = Lists.newArrayList(Iterables.transform(proxy, input -> input.getObjectId()));

            @Override
            public int compare(final RpslObject o1, final RpslObject o2) {
                return requestedIds.indexOf(o1.getObjectId()) - requestedIds.indexOf(o2.getObjectId());
            }
        });

        // TODO [AK] Return result rather than adding all to the collection
        result.addAll(rpslObjects);
    }

    private Set<Integer> loadObjects(final List<Identifiable> proxy, final Map<Integer, RpslObject> loadedObjects) {
        final StringBuilder queryBuilder = new StringBuilder();
        final List<Integer> objectIds = Lists.newArrayListWithExpectedSize(proxy.size());
        for (final Identifiable identifiable : proxy) {
            final Integer objectId = identifiable.getObjectId();
            if (loadedObjects.containsKey(objectId)) {
                continue;
            }

            if (identifiable instanceof RpslObject) {
                loadedObjects.put(objectId, (RpslObject) identifiable);
            } else {
                if (queryBuilder.length() > 0) {
                    // UNION ALL is much faster than IN
                    queryBuilder.append(" UNION ALL ");
                }

                queryBuilder.append("" +
                        "SELECT object_id, object " +
                        "FROM last " +
                        "WHERE object_id = ? " +
                        "AND sequence_id != 0");

                objectIds.add(objectId);
            }
        }

        final List<RpslObject> rpslObjects = jdbcTemplate.query(
                queryBuilder.toString(),
                new PreparedStatementSetter() {
                    @Override
                    public void setValues(final PreparedStatement ps) throws SQLException {
                        for (int i = 0; i < objectIds.size(); i++) {
                            ps.setInt(i + 1, objectIds.get(i));
                        }
                    }
                },
                new RpslObjectRowMapper());

        for (final RpslObject rpslObject : rpslObjects) {
            loadedObjects.put(rpslObject.getObjectId(), rpslObject);
        }

        if (proxy.size() == loadedObjects.size()) {
            return Collections.emptySet();
        }

        final Set<Integer> differences = Sets.newLinkedHashSet();
        for (final Identifiable identifiable : proxy) {
            final Integer objectId = identifiable.getObjectId();
            if (!loadedObjects.containsKey(objectId)) {
                differences.add(objectId);
            }
        }

        return differences;
    }

    @Override
    public RpslObject getById(final int objectId) {
        return JdbcRpslObjectOperations.getObjectById(jdbcTemplate, objectId);
    }

    @Override
    public RpslObject getByKey(final ObjectType type, final String key) {
        return getById(findByKey(type, key).getObjectId());
    }

    @Override
    @Nullable
    public RpslObject getByKeyOrNull(final ObjectType type, final String key) {
        final RpslObjectInfo rpslObjectInfo = findByKeyOrNull(type, key);
        if (rpslObjectInfo == null) {
            return null;
        }
        return getById(rpslObjectInfo.getObjectId());
    }

    @Override
    public RpslObject getByKey(final ObjectType type, final CIString key) {
        return getByKey(type, key.toString());
    }

    @Override
    @Nullable
    public RpslObject getByKeyOrNull(final ObjectType type, final CIString key) {
        return getByKeyOrNull(type, key.toString());
    }

    @Override
    public List<RpslObject> getByKeys(final ObjectType type, final Collection<CIString> searchKeys) {
        final List<RpslObject> result = new ArrayList<>(searchKeys.size());

        for (final CIString searchKey : searchKeys) {
            try {
                result.add(getByKey(type, searchKey));
            } catch (EmptyResultDataAccessException ignored) {
            }
        }

        return result;
    }

    @Override
    @CheckForNull
    public RpslObject findAsBlock(final long begin, final long end) {
        final List<RpslObject> asBlock = jdbcTemplate.query("" +
                        "SELECT l.object_id, l.object " +
                        "FROM last l " +
                        "JOIN as_block a ON l.object_id = a.object_id " +
                        "WHERE ? >= a.begin_as " +
                        "AND ? <= a.end_as " +
                        "AND l.sequence_id != 0",
                new RpslObjectRowMapper(),
                begin,
                end
        );

        return asBlock.isEmpty() ? null : asBlock.get(0);
    }

    @Override
    public List<RpslObjectInfo> findDomainByNetname(final CIString searchKey) {
        return findDomainByNetname(searchKey.toString());
    }

    @Override
    public List<RpslObjectInfo> findDomainByNetname(final String searchKey) {
        return jdbcTemplate.query(" " +
                "SELECT l.object_id, l.object_type, l.pkey " +
                "  FROM last l " +
                "  LEFT JOIN domain d ON l.object_id = d.object_id " +
                "  WHERE d.netname = ? " + 
                "  AND l.sequence_id != 0 ",
                new RpslObjectInfoResultSetExtractor(),
                searchKey);
    }

    @Override
    public List<RpslObject> findAsBlockIntersections(final long begin, final long end) {
        Map<String, Object> params = Maps.newHashMap();
        params.put("begin", begin);
        params.put("end", end);

        return new NamedParameterJdbcTemplate(jdbcTemplate).query("" +
                "SELECT l.object_id, l.object " +
                "FROM last l " +
                "JOIN as_block a ON l.object_id = a.object_id " +
                "WHERE (:begin >= a.begin_as AND :begin <=a.end_as) " +
                "OR (:end >= a.begin_as AND :end <=a.end_as) " +
                "OR (:begin<=a.begin_as AND :end >= a.end_as) " +
                "AND l.sequence_id != 0",
                params,
                new RpslObjectRowMapper()
        );
    }

    @Override
    public RpslObjectInfo findByKey(final ObjectType type, final CIString searchKey) {
        return findByKey(type, searchKey.toString());
    }

    @Override
    @Nullable
    public RpslObjectInfo findByKeyOrNull(final ObjectType type, final CIString searchKey) {
        return findByKeyOrNull(type, searchKey.toString());
    }

    @Override
    public RpslObjectInfo findByKey(final ObjectType type, final String searchKey) {
        RpslObjectInfo result = findByKeyOrNull(type, searchKey);
        if (result == null) {
            throw new EmptyResultDataAccessException(1);
        } else {
            return result;
        }
    }

    @Override
    @Nullable
    public RpslObjectInfo findByKeyOrNull(final ObjectType type, final String searchKey) {
        final List<RpslObjectInfo> objectInfos = findByKeyInIndex(type, searchKey);

        switch (objectInfos.size()) {
            case 0:
                return null;
            case 1:
                return objectInfos.get(0);
            default:
                throw new IncorrectResultSizeDataAccessException(String.format("Multiple objects found in key index for object [%s] %s", type, searchKey), 1, objectInfos.size());
        }
    }

    private List<RpslObjectInfo> findByKeyInIndex(final ObjectType type, final String key) {
        final AttributeType keyLookupAttribute = ObjectTemplate.getTemplate(type).getKeyLookupAttribute();
        final IndexStrategy indexStrategy = IndexStrategies.get(keyLookupAttribute);
        return indexStrategy.findInIndex(jdbcTemplate, key, type);
    }

    @Override
    public List<RpslObjectInfo> findByAttribute(final AttributeType attributeType, final String attributeValue) {
        final IndexStrategy indexStrategy = IndexStrategies.get(attributeType);
        return indexStrategy.findInIndex(jdbcTemplate, attributeValue);
    }

    @Override
    public List<RpslObjectInfo> findMemberOfByObjectTypeWithoutMbrsByRef(final ObjectType objectType, final String attributeValue) {
        final ObjectTemplate objectTemplate = ObjectTemplate.getTemplate(objectType);
        final Set<AttributeType> keyAttributes = objectTemplate.getKeyAttributes();
        Validate.isTrue(keyAttributes.size() == 1);

        final IndexStrategy indexStrategy = IndexStrategies.get(keyAttributes.iterator().next());

        final String query = MessageFormat.format(
                "SELECT l.object_id, l.object_type, l.pkey " +
                        "  FROM {0}, member_of" +
                        "  LEFT OUTER JOIN mnt_by ON member_of.object_id = mnt_by.object_id " +
                        "  LEFT JOIN last l ON l.object_id = member_of.object_id " +
                        "  WHERE {0}.object_id = member_of.set_id " +
                        "  AND {0}.{1} = ?" +
                        "  AND l.sequence_id != 0 ",
                indexStrategy.getLookupTableName(),
                indexStrategy.getLookupColumnName());

        return jdbcTemplate.query(query, new RpslObjectInfoResultSetExtractor(), attributeValue);
    }

    @Override
    public Collection<RpslObjectInfo> relatedTo(final RpslObject identifiable, final Set<ObjectType> excludeObjectTypes) {
        final LinkedHashSet<RpslObjectInfo> result = Sets.newLinkedHashSet();

        for (final RpslAttribute attribute : identifiable.findAttributes(RELATED_TO_ATTRIBUTES)) {
            for (final CIString referenceValue : attribute.getReferenceValues()) {
                for (final ObjectType objectType : attribute.getType().getReferences(referenceValue)) {
                    if (excludeObjectTypes.contains(objectType)) {
                        continue;
                    }

                    for (RpslObjectInfo rpslObjectInfo : findByKeyInIndex(objectType, referenceValue.toString())) {
                        if (rpslObjectInfo.getObjectId() != identifiable.getObjectId()) {
                            result.add(rpslObjectInfo);
                        }
                    }
                }
            }
        }

        return result;
    }

    @Override
    public Map<String, String> domains(){
        String sql = "SELECT d.domain, o.city_id FROM domain d LEFT JOIN organisation o ON d.org=o.organisation";
        return jdbcTemplate.query(sql, new ResultSetExtractor<Map<String, String>>() {
            @Override
            public Map<String, String> extractData(ResultSet rs) throws SQLException, DataAccessException {
                Map<String, String> result = Maps.newHashMap();
                while (rs.next()) {
                    result.put(rs.getString(1), rs.getString(2));
                }
                return result;
            }
        });
    }

    @Override
    public Map<Integer, Integer> ips(){
        // object_type: ipv4=6 and ipv6=5
        String sql = "SELECT object_type, count(*) FROM last WHERE sequence_id != 0 and (object_type = 5 or object_type = 6 or object_type = 3) GROUP BY object_type";
        return jdbcTemplate.query(sql, new ResultSetExtractor<Map<Integer, Integer>>() {
            @Override
            public Map<Integer, Integer> extractData(ResultSet rs) throws SQLException, DataAccessException {
                Map<Integer, Integer> result = Maps.newHashMap();
                while (rs.next()) {
                    result.put(rs.getInt(1), rs.getInt(2));
                }
                return result;
            }
        });
    }

    @Override
    public Map<String, LinkedHashMap<Integer, Integer>> offlines(){
        // object_type: ipv4=6 and ipv6=5
        LocalDate today = LocalDate.now();
        LocalDate twoMonthsAgo = today.minusMonths(2);
        DateTimeFormatter formatter = DateTimeFormatter.ofPattern("yyyy-MM-dd");
        String formattedDate = twoMonthsAgo.format(formatter);
        String sql = "SELECT object_type, timestamp, totalcount FROM statistics WHERE STR_TO_DATE(timestamp, '%Y-%m-%d') > " + formattedDate +" ORDER BY timestamp ASC";
        return jdbcTemplate.query(sql, new ResultSetExtractor<Map<String, LinkedHashMap<Integer, Integer>>>() {
            @Override
            public Map<String, LinkedHashMap<Integer, Integer>> extractData(ResultSet rs) throws SQLException, DataAccessException {
                Map<String, LinkedHashMap<Integer, Integer>> result = new LinkedHashMap<String, LinkedHashMap<Integer, Integer>>();
                while (rs.next()) {
                    LinkedHashMap<Integer, Integer> tmp_map = new LinkedHashMap<Integer, Integer>() {{put(rs.getInt(1), rs.getInt(3));}};
                    if(result.containsKey(rs.getString(2))){
                        tmp_map = result.get(rs.getString(2));
                        tmp_map.put(rs.getInt(1), rs.getInt(3));
                    }
                    result.put(rs.getString(2), tmp_map);
                }
                return result;
            }
        });
    }

    @Override
    public Map<String, Object> resourceStatistics(final LocalDate startDate,
                                                  final LocalDate endDate,
                                                  final boolean includeUndated) {
        final Map<Integer, String> cityIds = jdbcTemplate.query(
                "SELECT d.object_id, o.city_id FROM domain d " +
                        "LEFT JOIN organisation o ON d.org = o.organisation",
                rs -> {
                    final Map<Integer, String> result = new HashMap<>();
                    while (rs.next()) {
                        result.put(rs.getInt(1), rs.getString(2));
                    }
                    return result;
                });

        final Map<String, AddressFamilies> nameserverFamilies = loadNameserverFamilies();
        final Map<String, Integer> domainsByProvince = new LinkedHashMap<>();
        int domainCount = 0;
        int undatedDomains = 0;
        int domainsWithIpv4Nameserver = 0;
        int domainsWithIpv6Nameserver = 0;
        int domainsWithUnresolvedNameserver = 0;

        for (final RpslObject domain : loadCurrentObjects(ObjectType.DOMAIN)) {
            if (isInfrastructureDomain(domain.getKey().toString())) {
                continue;
            }
            final LocalDate registrationDate = registrationDate(domain);
            if (registrationDate == null) {
                undatedDomains++;
            }
            if (!withinPeriod(registrationDate, startDate, endDate, includeUndated)) {
                continue;
            }

            domainCount++;
            final String cityId = cityIds.get(domain.getObjectId());
            final String provinceCode = cityId == null || cityId.length() < 2 || cityId.startsWith("00")
                    ? "unknown"
                    : cityId.substring(0, 2);
            domainsByProvince.merge(provinceCode, 1, Integer::sum);

            boolean hasIpv4 = false;
            boolean hasIpv6 = false;
            boolean unresolved = false;
            for (final RpslAttribute nserver : domain.findAttributes(AttributeType.NSERVER)) {
                final String host = firstToken(nserver.getCleanValue().toString()).toLowerCase(Locale.ROOT);
                final AddressFamilies families = nameserverFamilies.get(host);
                if (families == null) {
                    unresolved = true;
                    continue;
                }
                hasIpv4 |= families.ipv4;
                hasIpv6 |= families.ipv6;
            }
            if (hasIpv4) {
                domainsWithIpv4Nameserver++;
            }
            if (hasIpv6) {
                domainsWithIpv6Nameserver++;
            }
            if (unresolved && !hasIpv4 && !hasIpv6) {
                domainsWithUnresolvedNameserver++;
            }
        }

        final CountResult ipv4 = countAllocations(ObjectType.INETNUM, startDate, endDate, includeUndated);
        final CountResult ipv6 = countAllocations(ObjectType.INET6NUM, startDate, endDate, includeUndated);

        final Map<String, Object> period = new LinkedHashMap<>();
        period.put("startDate", startDate == null ? null : startDate.toString());
        period.put("endDate", endDate == null ? null : endDate.toString());
        period.put("dateField", "registration-date");
        period.put("includeUndated", includeUndated);

        final Map<String, Object> domains = new LinkedHashMap<>();
        domains.put("total", domainCount);
        domains.put("undated", undatedDomains);
        domains.put("byProvince", domainsByProvince);

        final Map<String, Object> ipAllocations = new LinkedHashMap<>();
        ipAllocations.put("total", ipv4.included + ipv6.included);
        ipAllocations.put("ipv4", ipv4.included);
        ipAllocations.put("ipv6", ipv6.included);
        ipAllocations.put("undated", ipv4.undated + ipv6.undated);

        final Map<String, Object> nameserverCoverage = new LinkedHashMap<>();
        nameserverCoverage.put("domainsWithIpv4Nameserver", domainsWithIpv4Nameserver);
        nameserverCoverage.put("domainsWithIpv6Nameserver", domainsWithIpv6Nameserver);
        nameserverCoverage.put("domainsWithUnresolvedNameserver", domainsWithUnresolvedNameserver);

        final Map<String, Object> result = new LinkedHashMap<>();
        result.put("period", period);
        result.put("domains", domains);
        result.put("ipAllocations", ipAllocations);
        result.put("nameserverCoverage", nameserverCoverage);
        result.put("generatedAt", java.time.OffsetDateTime.now().toString());
        return result;
    }

    private List<RpslObject> loadCurrentObjects(final ObjectType objectType) {
        return jdbcTemplate.query(
                "SELECT object_id, object FROM last WHERE sequence_id != 0 AND object_type = ?",
                new RpslObjectRowMapper(), ObjectTypeIds.getId(objectType));
    }

    private Map<String, AddressFamilies> loadNameserverFamilies() {
        final Map<String, AddressFamilies> result = new HashMap<>();
        for (final RpslObject nameserver : loadCurrentObjects(ObjectType.NAMESERVER)) {
            final AddressFamilies families = new AddressFamilies();
            for (final RpslAttribute ipnum : nameserver.findAttributes(AttributeType.IPNUM)) {
                final String value = ipnum.getCleanValue().toString();
                families.ipv4 |= value.contains(".");
                families.ipv6 |= value.contains(":");
            }
            result.put(nameserver.getKey().toString().toLowerCase(Locale.ROOT), families);
        }
        return result;
    }

    private CountResult countAllocations(final ObjectType objectType,
                                         final LocalDate startDate,
                                         final LocalDate endDate,
                                         final boolean includeUndated) {
        int included = 0;
        int undated = 0;
        for (final RpslObject object : loadCurrentObjects(objectType)) {
            final LocalDate registrationDate = registrationDate(object);
            if (registrationDate == null) {
                undated++;
            }
            if (withinPeriod(registrationDate, startDate, endDate, includeUndated)) {
                included++;
            }
        }
        return new CountResult(included, undated);
    }

    static boolean withinPeriod(final LocalDate value,
                                final LocalDate startDate,
                                final LocalDate endDate,
                                final boolean includeUndated) {
        if (value == null) {
            return includeUndated;
        }
        return (startDate == null || !value.isBefore(startDate))
                && (endDate == null || !value.isAfter(endDate));
    }

    private static LocalDate registrationDate(final RpslObject object) {
        final List<RpslAttribute> attributes = object.findAttributes(AttributeType.REGISTRATION_DATE);
        if (attributes.isEmpty()) {
            return null;
        }
        try {
            return LocalDate.parse(attributes.get(0).getCleanValue().toString());
        } catch (RuntimeException ignored) {
            return null;
        }
    }

    private static boolean isInfrastructureDomain(final String domain) {
        final String normalized = domain.toLowerCase(Locale.ROOT);
        return normalized.equals("in-addr.arpa") || normalized.equals("ip6.arpa") || normalized.equals("e164.arpa");
    }

    private static String firstToken(final String value) {
        final int separator = value.indexOf(' ');
        return separator < 0 ? value : value.substring(0, separator);
    }

    private static final class AddressFamilies {
        private boolean ipv4;
        private boolean ipv6;
    }

    private record CountResult(int included, int undated) {}
}
