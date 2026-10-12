import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

import net.ripe.db.whois.common.rpsl.ObjectMessages;
import net.ripe.db.whois.common.rpsl.ObjectTemplate;
import net.ripe.db.whois.common.rpsl.RpslObject;

/** Parse and template-validate every object in a generated WHOIS dump. */
public final class ValidateRpslDump {
    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            throw new IllegalArgumentException("usage: ValidateRpslDump <dump>");
        }
        String input = Files.readString(Path.of(args[0]), StandardCharsets.UTF_8);
        String[] blocks = input.split("(?:\\r?\\n){2,}");
        List<String> failures = new ArrayList<>();
        int parsed = 0;
        int templateErrors = 0;
        for (int index = 0; index < blocks.length; index++) {
            if (blocks[index].isBlank()) continue;
            try {
                RpslObject object = RpslObject.parse(blocks[index].strip() + "\n");
                ObjectMessages messages = ObjectTemplate.getTemplate(object.getType()).validate(object);
                if (messages.hasErrors()) {
                    templateErrors += messages.getErrorCount();
                }
                parsed++;
            } catch (RuntimeException error) {
                failures.add("object " + (index + 1) + ": " + error.getMessage());
            }
            if (failures.size() >= 100) break;
        }
        if (!failures.isEmpty()) {
            failures.forEach(System.err::println);
            System.exit(1);
        }
        System.out.println("RPSL parsing passed: " + parsed + " objects; legacy template warnings: " + templateErrors);
    }
}
