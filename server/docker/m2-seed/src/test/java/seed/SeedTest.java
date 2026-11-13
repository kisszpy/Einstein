package seed;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

class SeedTest {

    @Test
    void lombokGetterSetterWorks() {
        Seed seed = new Seed();
        seed.setName("ok");
        assertEquals("ok", seed.getName());
    }
}
