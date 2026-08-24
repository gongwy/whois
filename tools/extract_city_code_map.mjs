#!/usr/bin/env node

import { writeFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

const source = process.argv[2]
    ? pathToFileURL(process.argv[2]).href
    : new URL('../../whois_website/src/main/resources/static/constant.js', import.meta.url).href;
const { province_info, strict_info } = await import(source);

const output = process.argv[3];
if (!output) {
    throw new Error('Usage: node tools/extract_city_code_map.mjs /path/to/constant.js /path/to/city-codes.json');
}

const cities = {};
const locations = {};
for (const [city, districts] of Object.entries(strict_info)) {
    const entries = Object.entries(districts).filter(([name, code]) => name !== '---请选择---' && code);
    const cityEntry = entries.find(([name]) => name === '市辖区') || entries[0];
    if (cityEntry) {
        cities[city] = String(cityEntry[1]);
    }
    for (const [name, code] of entries) {
        (locations[name] ??= []).push(String(code));
    }
}

writeFileSync(output, JSON.stringify({
    provinces: Object.fromEntries(Object.entries(province_info).map(([name, data]) => [name, String(data.province_code)])),
    cities,
    locations,
}, null, 2) + '\n');
