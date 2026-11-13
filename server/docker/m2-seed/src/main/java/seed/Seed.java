package seed;

import java.util.List;

import com.fasterxml.jackson.databind.ObjectMapper;

import lombok.Data;

/**
 * 预热用占位类。
 *
 * 存在的唯一目的是让 `mvn package` 真的走一遍编译 + 测试生命周期：
 * 只有真编译，compiler/surefire/resources 这些插件和它们的依赖才会被
 * 下载进 /opt/m2。写个空 pom 再 `dependency:go-offline` 是不够的——
 * go-offline 拿不到插件传递依赖，运行时第一次 `mvn compile` 照样卡在
 * 「plugin not found (offline)」。
 *
 * 顺带验证 lombok 在 JDK 21 上的注解处理器可用（模型很爱写 @Data）。
 */
@Data
public class Seed {

    private String name;
    private int value;

    public static void main(String[] args) throws Exception {
        Seed seed = new Seed();
        seed.setName("einstein");
        seed.setValue(1);
        List<Seed> all = List.of(seed);
        System.out.println(new ObjectMapper().writeValueAsString(all));
    }
}
