package net.ripe.db.whois.common.dao.jdbc.index;

import net.ripe.db.whois.common.dao.RpslObjectInfo;
import net.ripe.db.whois.common.dao.jdbc.domain.RpslObjectInfoResultSetExtractor;
import net.ripe.db.whois.common.domain.CIString;
import net.ripe.db.whois.common.rpsl.AttributeType;
import net.ripe.db.whois.common.rpsl.RpslObject;
import org.springframework.jdbc.core.JdbcTemplate;

import java.text.MessageFormat;
import java.util.List;

class IndexWithOrganisation extends IndexStrategySimpleLookup {

    protected IndexWithOrganisation(final AttributeType attributeType, final String lookupTableName, final String lookupColumnName) {
        super(attributeType, lookupTableName, lookupColumnName);
    }

    @Override
    public int addToIndex(final JdbcTemplate jdbcTemplate, final RpslObjectInfo objectInfo, final RpslObject object, final String value) {
        final CIString cityIdAttribute = object.getValueOrNullForAttribute(AttributeType.C_CITY_ID);
        final String cityid = cityIdAttribute == null ? "" : cityIdAttribute.toString();
        final String query = String.format("INSERT INTO %s (object_id, %s, city_id) VALUES (?, ?, ?)", lookupTableName, lookupColumnName);
        return jdbcTemplate.update(query, objectInfo.getObjectId(), value, cityid);
    }

    @Override
    public List<RpslObjectInfo> findInIndex(final JdbcTemplate jdbcTemplate, final String value) {
        // FIXME: [AH] joining to last is very costly and unnecessary here; look for ways to drop this join
        final String query = MessageFormat.format("" +
                "SELECT l.object_id, l.object_type, l.pkey " +
                "  FROM {0} " +
                "  LEFT JOIN last l ON l.object_id = {0}.object_id " +
                "  WHERE {0}.{1} = ? " +
                "  AND l.sequence_id != 0 ",
                lookupTableName,
                lookupColumnName
            );

        return jdbcTemplate.query(query, new RpslObjectInfoResultSetExtractor(), value);
    }
}
