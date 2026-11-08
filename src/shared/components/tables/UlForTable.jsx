import React from "react";
import { Image } from "@chakra-ui/react";
import chevronUp from "shared/assets/images/chevron-up.svg";
import noSort from "shared/assets/images/no-sort.svg";
import styles from "shared/components/tables/ulForTable.module.css";

// A column header. When sort is undefined the header is inert, which is why the
// toggle only renders for sortable columns.
const UlForTable = ({ sort, setSort, name }) => (
  <ul className={styles.UlForTable}>
    <li className={`${styles.UlForTable__li_first} ${styles.UlForTable__li}`}>
      {name}
    </li>
    {typeof sort === "undefined" ? null : (
      <li className={styles.UlForTable__li}>
        <Image
          src={sort ? chevronUp : noSort}
          w="16px"
          h="16px"
          alt=""
          cursor="pointer"
          onClick={() => setSort((prev) => !prev)}
        />
      </li>
    )}
  </ul>
);

export default UlForTable;
