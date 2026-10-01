// C5-truss only
MATCH (:User {user_id: $user})-[r:CROSS_RECOMMEND]->(i:Item)
RETURN i.item_id, i.title, r.max_c5_role_edge_tau_c5
ORDER BY r.max_c5_role_edge_tau_c5 DESC,
         i.training_item_degree DESC, i.item_id
LIMIT $limit;

// ItemKNN baseline
MATCH (:User {user_id: $user})-[r:CROSS_RECOMMEND]->(i:Item)
RETURN i.item_id, i.title, r.itemknn
ORDER BY r.itemknn DESC, i.training_item_degree DESC, i.item_id
LIMIT $limit;

