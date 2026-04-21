在前面文章[MyBatis配置文件解析过程](http://www.zhangdx.cn/article/2006371423.html)了解了注册`MappedStatement`的逻辑，本节详细看一下，构建`MappedStatement`的过程，也就是解析SQL语句定义（Mapper xml文件定义和Mapper接口方法注解定义）的逻辑。

## 解析Mapper xml文件

由前文所示，通过配置文件中`<mapper/>`标签引入Mapper xml文件，在解析配置文件时就触发解析构建。每个xml文件交给`XMLMapperBuilder`对象来完成解析构建。

```java
public class XMLMapperBuilder extends BaseBuilder {
    // 具体xml文件解析器，前文说过
    private final XPathParser parser;
    // Mapper创建助理对象，负责使用相应的生成器模式类创建相应实例
    private final MapperBuilderAssistant builderAssistant;
    // 缓存的动态SQL片段节点
    private final Map<String, XNode> sqlFragments;
    // 当前对象要解析的xml文件资源对象
    private final String resource;
    // 解析主方法
    public void parse() {
        if (!configuration.isResourceLoaded(resource)) {
            // 处理mapper标签内的信息，主要逻辑
            configurationElement(parser.evalNode("/mapper"));
            // 标记资源已经加载
            configuration.addLoadedResource(resource);
            // 绑定命名空间关联的Mapper接口
            bindMapperForNamespace();
        }
        // 延迟解析未处理的一些元素
        parsePendingResultMaps();
        parsePendingCacheRefs();
        parsePendingStatements();
    }

    private void configurationElement(XNode context) {
        try {
            // 获取定义的命名空间
            String namespace = context.getStringAttribute("namespace");
            if (namespace == null || namespace.isEmpty()) {
                throw new BuilderException("Mapper's namespace cannot be empty");
            }
            builderAssistant.setCurrentNamespace(namespace);
            // 如下就是解析各个子标签的具体方法
            cacheRefElement(context.evalNode("cache-ref"));
            cacheElement(context.evalNode("cache"));
            parameterMapElement(context.evalNodes("/mapper/parameterMap"));
            resultMapElements(context.evalNodes("/mapper/resultMap"));
            sqlElement(context.evalNodes("/mapper/sql"));
            buildStatementFromContext(context.evalNodes("select|insert|update|delete"));
        } catch (Exception e) {
            throw new BuilderException("Error parsing Mapper XML. The XML location is '" + resource + "'. Cause: " + e, e);
        }
    }
}
```

### 解析select、insert等标签

这里批量获取`<select/>`、`<insert/>`、`<update/>`、`<delete/>`这四类定义SQL语句的标签，每个都对应一个`MappedStatement`，这里交给`XMLStatementBuilder`具体处理内部的属性和动态SQL语句。

```java
public class XMLMapperBuilder extends BaseBuilder {
    private void buildStatementFromContext(List<XNode> list) {
        if (configuration.getDatabaseId() != null) {
            buildStatementFromContext(list, configuration.getDatabaseId());
        }
        buildStatementFromContext(list, null);
    }

    private void buildStatementFromContext(List<XNode> list, String requiredDatabaseId) {
        for (XNode context : list) {
            final XMLStatementBuilder statementParser = new XMLStatementBuilder(configuration, builderAssistant, context, requiredDatabaseId);
            try {
                // 解析Statement节点
                statementParser.parseStatementNode();
            } catch (IncompleteElementException e) {
                // 内部可能抛出IncompleteElementException异常，就先将解析器对象存储
                configuration.addIncompleteStatement(statementParser);
            }
        }
    }
}
```

这一部分存有一个逻辑点是`XMLStatementBuilder`解析过程中可能抛出`IncompleteElementException`，这种情况是代表解析到某个元素时，它依赖的对象还没有准备好，暂时无法完成解析。

可以思考下列场景：<font style="color:rgb(73, 246, 181);">a文件中的依赖的元素定义在b文件中，且解析顺序是无序的，则需要等所有xml解析完，再延迟解析这一个依赖关系</font>。就需要一个标记解析不完整的标记`IncompleteElementException`。例如下面这些元素会存在依赖的问题。

```xml
<mapper>
  <resultMap id="userMap" extends="baseMap"/>
  <association resultMap="otherMapper.otherMap"/>
  <select id="selectUser" resultMap="userMap"></select>
  <include refid="base_columns"/>
</mapper>
```

接下来深入`XMLStatementBuilder`看是如何解析的，以及如何抛出`IncompleteElementException`。

```java
public class XMLStatementBuilder extends BaseBuilder {
    private final MapperBuilderAssistant builderAssistant;
    private final XNode context;
    private final String requiredDatabaseId;

    public void parseStatementNode() {
        // 取id属性，比较重要，需要关联Mapper接口的方法名
        String id = context.getStringAttribute("id");
        String databaseId = context.getStringAttribute("databaseId");
        // 如果标签上定义的数据库标识和当前激活的数据库标识不一致，就不解析当前的SQL定义。
        if (!databaseIdMatchesCurrent(id, databaseId, this.requiredDatabaseId)) {
            return;
        }
        String nodeName = context.getNode().getNodeName();
        // 根据标签名称映射出SQL命令的类型：对应增删改查和FLUSH
        SqlCommandType sqlCommandType = SqlCommandType.valueOf(nodeName.toUpperCase(Locale.ENGLISH));
        boolean isSelect = sqlCommandType == SqlCommandType.SELECT;
        boolean flushCache = context.getBooleanAttribute("flushCache", !isSelect);
        boolean useCache = context.getBooleanAttribute("useCache", isSelect);
        boolean resultOrdered = context.getBooleanAttribute("resultOrdered", false);

        // 优先处理子标签<include/>
        XMLIncludeTransformer includeParser = new XMLIncludeTransformer(configuration, builderAssistant);
        includeParser.applyIncludes(context.getNode());
        // 获取参数类型属性，并解析出对应的类
        String parameterType = context.getStringAttribute("parameterType");
        Class<?> parameterTypeClass = resolveClass(parameterType);
        // 处理数据库方言的信息
        String lang = context.getStringAttribute("lang");
        LanguageDriver langDriver = getLanguageDriver(lang);
        // 然后再解析<selectKey/>，解析完之后删除
        processSelectKeyNodes(id, parameterTypeClass, langDriver);

        // <selectKey>和<include>解析完并删除后，开始解析SQL片段
        KeyGenerator keyGenerator;
        String keyStatementId = id + SelectKeyGenerator.SELECT_KEY_SUFFIX;
        // 构建keyStatementId，大部分情况就是命名空间和id用英文句号拼接，类似于类中方法名的引用
        keyStatementId = builderAssistant.applyCurrentNamespace(keyStatementId, true);
        // 判断获取key生成器，可以自定义，默认是使用内部定义的
        if (configuration.hasKeyGenerator(keyStatementId)) {
            keyGenerator = configuration.getKeyGenerator(keyStatementId);
        } else {
            keyGenerator = context.getBooleanAttribute("useGeneratedKeys",
                                                       configuration.isUseGeneratedKeys() && SqlCommandType.INSERT.equals(sqlCommandType))
            ? Jdbc3KeyGenerator.INSTANCE : NoKeyGenerator.INSTANCE;
        }
        // 解析出原始动态SQL对象SqlSource
        SqlSource sqlSource = langDriver.createSqlSource(configuration, context, parameterTypeClass);
        // 解析出这一批属性定义的值，对应我们属性的一些设置项
        StatementType statementType = StatementType.valueOf(context.getStringAttribute("statementType", StatementType.PREPARED.toString()));
        Integer fetchSize = context.getIntAttribute("fetchSize");
        Integer timeout = context.getIntAttribute("timeout");
        String parameterMap = context.getStringAttribute("parameterMap");
        String resultType = context.getStringAttribute("resultType");
        Class<?> resultTypeClass = resolveClass(resultType);
        String resultMap = context.getStringAttribute("resultMap");
        String resultSetType = context.getStringAttribute("resultSetType");
        // 确定要使用的结果集类型
        ResultSetType resultSetTypeEnum = resolveResultSetType(resultSetType);
        if (resultSetTypeEnum == null) {
            resultSetTypeEnum = configuration.getDefaultResultSetType();
        }
        String keyProperty = context.getStringAttribute("keyProperty");
        String keyColumn = context.getStringAttribute("keyColumn");
        String resultSets = context.getStringAttribute("resultSets");
        // 把解析出的各个部分信息交给构建助理对象处理
        builderAssistant.addMappedStatement(id, sqlSource, statementType, sqlCommandType,
                                            fetchSize, timeout, parameterMap, parameterTypeClass, resultMap, resultTypeClass,
                                            resultSetTypeEnum, flushCache, useCache, resultOrdered,
                                            keyGenerator, keyProperty, keyColumn, databaseId, langDriver, resultSets);
    }
}
```

从xml标签中解析出对应的信息后，就交给`MapperBuilderAssistant`来构建`MappedStatement`对象了。

1. 这些标签解析出来都会生成一个`MappedStatement`对象，存储到`Configuration`中。
2. `id`是最重要的属性，它和命名空间组成一个`MappedStatement`实例的唯一标识，在MyBatis框架执行SQL的流程中，主要通过这个标识来获取`MappedStatement`实例。

### 解析cache和cache-ref标签

`<cache/>`标签是用来定义当前Mapper的二级缓存（简单格式如下），即为当前命名空间创建一个二级缓存实例（本质是`Cache`对象）。

```xml
<cache eviction="LRU" flushInterval="60000" size="512" readOnly="true"/>
```

```java
public class XMLMapperBuilder extends BaseBuilder {
    private void cacheElement(XNode context) {
        if (context != null) {
            // 获取定义的缓存类类型，默认使用的是PerpetualCache
            String type = context.getStringAttribute("type", "PERPETUAL");
            Class<? extends Cache> typeClass = typeAliasRegistry.resolveAlias(type);
            // 获取定义的缓存清楚策略，默认是LRU，LruCache是一个缓存装饰器类
            String eviction = context.getStringAttribute("eviction", "LRU");
            Class<? extends Cache> evictionClass = typeAliasRegistry.resolveAlias(eviction);
            // 获取定义的缓存自动刷新间隔，默认不自动刷新，只有在调用语句时才触发刷新
            Long flushInterval = context.getLongAttribute("flushInterval");
            // 获取其他属性
            Integer size = context.getIntAttribute("size");
            boolean readWrite = !context.getBooleanAttribute("readOnly", false);
            boolean blocking = context.getBooleanAttribute("blocking", false);
            Properties props = context.getChildrenAsProperties();
            // 构建缓存对象
            builderAssistant.useNewCache(typeClass, evictionClass, flushInterval, size, readWrite, blocking, props);
        }
    }
}

public class MapperBuilderAssistant extends BaseBuilder {
    public Cache useNewCache(Class<? extends Cache> typeClass,
                             Class<? extends Cache> evictionClass,
                             Long flushInterval,
                             Integer size,
                             boolean readWrite,
                             boolean blocking,
                             Properties props) {
        Cache cache = new CacheBuilder(currentNamespace)
        .implementation(valueOrDefault(typeClass, PerpetualCache.class))
        .addDecorator(valueOrDefault(evictionClass, LruCache.class))
        .clearInterval(flushInterval)
        .size(size)
        .readWrite(readWrite)
        .blocking(blocking)
        .properties(props)
        .build();
        configuration.addCache(cache);
        currentCache = cache;
        return cache;
    }
}
```

而`<cache-ref/>`标签则可以定义复用其他Mapper内定义的缓存，这样就可以在多个命名空间中共享缓存配置和实例。

```xml
<cache-ref namespace="com.xxx.BaseMapper"/>
```

缓存引用定义的解析涉及到一个延迟解析的机制（上面出现过），需要引用的缓存定义已经被加载，

```java
public class XMLMapperBuilder extends BaseBuilder {
    private void cacheRefElement(XNode context) {
        if (context != null) {
            // 注册缓存引用关系，内部是个Map，key是当前命名空间，value是引用的命名空间
            configuration.addCacheRef(builderAssistant.getCurrentNamespace(), context.getStringAttribute("namespace"));
            CacheRefResolver cacheRefResolver = new CacheRefResolver(builderAssistant, context.getStringAttribute("namespace"));
            try {
                // 缓存引用解析器解析
                cacheRefResolver.resolveCacheRef();
            } catch (IncompleteElementException e) {
                // 如果内部报这个错误的话，说明引用的缓存还没有解析，要延迟处理
                configuration.addIncompleteCacheRef(cacheRefResolver);
            }
        }
    }
}

public class CacheRefResolver {
    private final MapperBuilderAssistant assistant;
    private final String cacheRefNamespace;

    public CacheRefResolver(MapperBuilderAssistant assistant, String cacheRefNamespace) {
        this.assistant = assistant;
        this.cacheRefNamespace = cacheRefNamespace;
    }

    public Cache resolveCacheRef() {
        return assistant.useCacheRef(cacheRefNamespace);
    }
}

public class MapperBuilderAssistant extends BaseBuilder {
    private boolean unresolvedCacheRef;
    
    public Cache useCacheRef(String namespace) {
        if (namespace == null) {
            throw new BuilderException("cache-ref element requires a namespace attribute.");
        }
        try {
            unresolvedCacheRef = true;
            Cache cache = configuration.getCache(namespace);
            // 如果当前引用的缓存实例还没有被解析，就报错等后面的延迟加载
            if (cache == null) {
                throw new IncompleteElementException("No cache for namespace '" + namespace + "' could be found.");
            }
            // 否则就能够直接获取到引用的缓存实例
            currentCache = cache;
            unresolvedCacheRef = false;
            return cache;
        } catch (IllegalArgumentException e) {
            throw new IncompleteElementException("No cache for namespace '" + namespace + "' could be found.", e);
        }
    }
}
```

这部分解析逻辑主要就是三个步骤：

1. 将缓存引用关系注册到`Configuration`中，内部是个`Map`，key是当前命名空间，value是引用的命名空间。
2. 构建缓存引用解析器，并解析该引用。如果引用的那个缓存还没有被解析，就抛出`IncompleteElementException`异常，否则直接获取引用的缓存实例。
3. 如果解析过程中抛出`IncompleteElementException`异常，就暂存到`Configuration.incompleteCacheRefs`中，等待延迟解析。

### 解析sql标签

这个标签是用来定义可以复用的动态SQL片段的，其他SQL语句定义的标签内可以通过`<include/>`来使用。

> 详细使用案例参考：[官方文档](https://mybatis.p2hp.com/sqlmap-xml.html)

它解析的步骤很简单，每个`<sql/>`标签都需要有一个唯一标识id属性，只是把取出的内容放入到`sqlFragments`中。

```java
public class XMLMapperBuilder extends BaseBuilder {
    private final MapperBuilderAssistant builderAssistant;
    private final Map<String, XNode> sqlFragments;
    
    private void sqlElement(List<XNode> list) {
        if (configuration.getDatabaseId() != null) {
            sqlElement(list, configuration.getDatabaseId());
        }
        sqlElement(list, null);
    }

    private void sqlElement(List<XNode> list, String requiredDatabaseId) {
        for (XNode context : list) {
            String databaseId = context.getStringAttribute("databaseId");
            String id = context.getStringAttribute("id");
            id = builderAssistant.applyCurrentNamespace(id, false);
            if (databaseIdMatchesCurrent(id, databaseId, requiredDatabaseId)) {
                sqlFragments.put(id, context);
            }
        }
    }
}
```

### 解析resultMap标签

该标签也是一个很重要且常定义的一个标签，用来定义高级结果映射。如何使用这里就不做介绍了，但是因其支持的功能比较多，所以解析过程是很长很复杂的。

```java
public class XMLMapperBuilder extends BaseBuilder {
    private void resultMapElements(List<XNode> list) {
        for (XNode resultMapNode : list) {
            try {
                resultMapElement(resultMapNode);
            } catch (IncompleteElementException e) {
                // ignore, it will be retried
            }
        }
    }

    private ResultMap resultMapElement(XNode resultMapNode) {
        return resultMapElement(resultMapNode, Collections.emptyList(), null);
    }

    private ResultMap resultMapElement(XNode resultMapNode, List<ResultMapping> additionalResultMappings, Class<?> enclosingType) {
        ErrorContext.instance().activity("processing " + resultMapNode.getValueBasedIdentifier());
        // 取出结果映射支持的Java类型，这四个属性作用一样，只是按照优先级获取而已。使用第一个定义的属性
        String type = resultMapNode.getStringAttribute("type",
                                                       resultMapNode.getStringAttribute("ofType",
                                                                                        resultMapNode.getStringAttribute("resultType",
                                                                                                                         resultMapNode.getStringAttribute("javaType"))));
        Class<?> typeClass = resolveClass(type); // 解析出对应的Java类
        if (typeClass == null) {
            typeClass = inheritEnclosingType(resultMapNode, enclosingType);
        }
        Discriminator discriminator = null;
        List<ResultMapping> resultMappings = new ArrayList<>(additionalResultMappings);
        List<XNode> resultChildren = resultMapNode.getChildren();
        for (XNode resultChild : resultChildren) {
            if ("constructor".equals(resultChild.getName())) {
                // 处理子标签constructor
                processConstructorElement(resultChild, typeClass, resultMappings);
            } else if ("discriminator".equals(resultChild.getName())) {
                // 处理子标签discriminator
                discriminator = processDiscriminatorElement(resultChild, typeClass, resultMappings);
            } else {
                // 处理其他子标签，id、association、collection
                List<ResultFlag> flags = new ArrayList<>();
                if ("id".equals(resultChild.getName())) {
                    flags.add(ResultFlag.ID);
                }
                resultMappings.add(buildResultMappingFromContext(resultChild, typeClass, flags));
            }
        }
        String id = resultMapNode.getStringAttribute("id",
                                                     resultMapNode.getValueBasedIdentifier());
        String extend = resultMapNode.getStringAttribute("extends");
        Boolean autoMapping = resultMapNode.getBooleanAttribute("autoMapping");
        ResultMapResolver resultMapResolver = new ResultMapResolver(builderAssistant, id, typeClass, extend, discriminator, resultMappings, autoMapping);
        try {
            return resultMapResolver.resolve();
        } catch (IncompleteElementException e) {
            // 暂存到Configuration中，待延迟解析
            configuration.addIncompleteResultMap(resultMapResolver);
            throw e;
        }
    }

    protected Class<?> inheritEnclosingType(XNode resultMapNode, Class<?> enclosingType) {
        if ("association".equals(resultMapNode.getName()) && resultMapNode.getStringAttribute("resultMap") == null) {
            String property = resultMapNode.getStringAttribute("property");
            if (property != null && enclosingType != null) {
                MetaClass metaResultType = MetaClass.forClass(enclosingType, configuration.getReflectorFactory());
                return metaResultType.getSetterType(property);
            }
        } else if ("case".equals(resultMapNode.getName()) && resultMapNode.getStringAttribute("resultMap") == null) {
            return enclosingType;
        }
        return null;
    }
    // 解析子标签constructor
    private void processConstructorElement(XNode resultChild, Class<?> resultType, List<ResultMapping> resultMappings) {
        List<XNode> argChildren = resultChild.getChildren();
        for (XNode argChild : argChildren) {
            List<ResultFlag> flags = new ArrayList<>();
            flags.add(ResultFlag.CONSTRUCTOR);
            if ("idArg".equals(argChild.getName())) {
                flags.add(ResultFlag.ID);
            }
            resultMappings.add(buildResultMappingFromContext(argChild, resultType, flags));
        }
    }

    private Discriminator processDiscriminatorElement(XNode context, Class<?> resultType, List<ResultMapping> resultMappings) {
        // 取出discriminator标签的属性
        String column = context.getStringAttribute("column");
        String javaType = context.getStringAttribute("javaType");
        String jdbcType = context.getStringAttribute("jdbcType");
        String typeHandler = context.getStringAttribute("typeHandler");
        Class<?> javaTypeClass = resolveClass(javaType);
        Class<? extends TypeHandler<?>> typeHandlerClass = resolveClass(typeHandler);
        JdbcType jdbcTypeEnum = resolveJdbcType(jdbcType);
        Map<String, String> discriminatorMap = new HashMap<>();
        // 处理子标签case
        for (XNode caseChild : context.getChildren()) {
            String value = caseChild.getStringAttribute("value");
            // 取出定义的resultMap，如果不存在就解析内部引用的信息
            String resultMap = caseChild.getStringAttribute("resultMap", processNestedResultMappings(caseChild, resultMappings, resultType));
            discriminatorMap.put(value, resultMap);
        }
        return builderAssistant.buildDiscriminator(resultType, column, javaTypeClass, jdbcTypeEnum, typeHandlerClass, discriminatorMap);
    }
    // 解析resultMap子标签的公共方法
    private ResultMapping buildResultMappingFromContext(XNode context, Class<?> resultType, List<ResultFlag> flags) {
        // 解析property定义，其中constructor标签是name属性
        String property;
        if (flags.contains(ResultFlag.CONSTRUCTOR)) {
            property = context.getStringAttribute("name");
        } else {
            property = context.getStringAttribute("property");
        }
        // 解析column、javaType、jdcbType等属性
        String column = context.getStringAttribute("column");
        String javaType = context.getStringAttribute("javaType");
        String jdbcType = context.getStringAttribute("jdbcType");
        String nestedSelect = context.getStringAttribute("select");
        String nestedResultMap = context.getStringAttribute("resultMap", () ->
                                                            processNestedResultMappings(context, Collections.emptyList(), resultType));
        String notNullColumn = context.getStringAttribute("notNullColumn");
        String columnPrefix = context.getStringAttribute("columnPrefix");
        String typeHandler = context.getStringAttribute("typeHandler");
        String resultSet = context.getStringAttribute("resultSet");
        String foreignColumn = context.getStringAttribute("foreignColumn");
        boolean lazy = "lazy".equals(context.getStringAttribute("fetchType", configuration.isLazyLoadingEnabled() ? "lazy" : "eager"));
        Class<?> javaTypeClass = resolveClass(javaType);
        Class<? extends TypeHandler<?>> typeHandlerClass = resolveClass(typeHandler);
        JdbcType jdbcTypeEnum = resolveJdbcType(jdbcType);
        return builderAssistant.buildResultMapping(resultType, property, column, javaTypeClass, jdbcTypeEnum, nestedSelect, nestedResultMap, notNullColumn, columnPrefix, typeHandlerClass, flags, resultSet, foreignColumn, lazy);
    }
    // 解析association、collection、case标签下的内置resultMap
    private String processNestedResultMappings(XNode context, List<ResultMapping> resultMappings, Class<?> enclosingType) {
        if (Arrays.asList("association", "collection", "case").contains(context.getName())
            && context.getStringAttribute("select") == null) {
            validateCollection(context, enclosingType);
            ResultMap resultMap = resultMapElement(context, resultMappings, enclosingType);
            return resultMap.getId();
        }
        return null;
    }
}
```

对于`<resultMap/>`标签的解析整体来说是分为属性解析和子标签解析，其中子标签可有这些：`<constructor/>`、`<discriminator/>`、`<id/>`、`<result/>`、`<association/>`、`<collection/>`。前两个标签需要特殊处理，最终和其他标签都由方法`buildResultMappingFromContext()`统一处理，并处理成`ResultMapping`对象。

+ 该方法第一个逻辑是先要确定当前定义的结果映射要支持的Java类是什么，标签属性可通过`type`、`ofType`、`resultType`、`javaType`四个属性来声明，源码中通过该顺序依次获取，取第一个生命的属性来
+ 看`<constructor/>`的方法`processConstructorElement()`。该方法自身的逻辑很简单，就是对每个子标签（`<idArg/>`和`<arg/>`两种）都加了一个`ResultFlag.CONSTRUCTOR`的标识，且对于`<idArg/>`标签额外加了`ResultFlag.ID`标识。然后交给`buildResultMappingFromContext()`处理。
+ `<discriminator/>`的处理方法`processDiscriminatorElement()`，该标签是定义鉴别器的，子标签支持`<case/>`，
+ 最后说一下`buildResultMappingFromContext()`方法，该方法是一个通用的解析单个resultMap的方法（即针对一个`column`到一个`property`的映射，可以来自`<id/>`、`<result/>`）。
  - `property`的定义取值来源是`<result/>`标签的`property`属性以及`<constructor/>`标签的`name`属性。该属性定义是确定映射到Java类的哪一个属性（成员变量）。
  - 另一个比较重要的属性是`column`，定义使用SQL结果的哪一列来映射。

## 解析Mapper注解

除了xml文件的方式，`Mapper`还支持在接口方法上声明注解定义SQL语句，因为注解定义是依附于`Mapper`接口的，所以注解解析的入口是在`Mapper`注册之后。

```java
public class MapperRegistry {
    public <T> void addMapper(Class<T> type) {
        if (type.isInterface()) {
            if (hasMapper(type)) {
                throw new BindingException("Type " + type + " is already known to the MapperRegistry.");
            }
            boolean loadCompleted = false;
            try {
                knownMappers.put(type, new MapperProxyFactory<>(type));
                // 使用Mapper注解创建者解析
                MapperAnnotationBuilder parser = new MapperAnnotationBuilder(config, type);
                parser.parse();
                loadCompleted = true;
            } finally {
                if (!loadCompleted) {
                    knownMappers.remove(type);
                }
            }
        }
    }
}
```

```java
public class MapperAnnotationBuilder {
    private static final Set<Class<? extends Annotation>> statementAnnotationTypes = Stream
    .of(Select.class, Update.class, Insert.class, Delete.class, SelectProvider.class, UpdateProvider.class,
        InsertProvider.class, DeleteProvider.class)
    .collect(Collectors.toSet());

    private final Configuration configuration;
    private final MapperBuilderAssistant assistant;
    private final Class<?> type;
    public void parse() {
        String resource = type.toString();
        if (!configuration.isResourceLoaded(resource)) {
            // 加载解析xml文件（可能前面已经加载过，这里就直接跳过了）
            loadXmlResource();
            configuration.addLoadedResource(resource);
            // 设置命名空间
            assistant.setCurrentNamespace(type.getName());
            // 解析缓存注解
            parseCache();
            // 解析缓存引用注解
            parseCacheRef();
            // 循环处理接口中的方法
            for (Method method : type.getMethods()) {
                // 过滤掉非用户级别的方法
                if (!canHaveStatement(method)) {
                    continue;
                }
                // 如果存在@Select以及@ResultMap注解，就解析ResultMap的定义
                if (getAnnotationWrapper(method, false, Select.class, SelectProvider.class).isPresent()
                    && method.getAnnotation(ResultMap.class) == null) {
                    parseResultMap(method);
                }
                try {
                    // 解析方法注解的核心方法
                    parseStatement(method);
                } catch (IncompleteElementException e) {
                    // 如果抛出异常，暂存当前方法解析实例，待后续解析
                    configuration.addIncompleteMethod(new MethodResolver(this, method));
                }
            }
        }
        parsePendingMethods();
    }

    private void loadXmlResource() {
        // 加载关联的xml文件，需要和Mapper接口包的路径相同，但是这里的xml文件不是必须的
        if (!configuration.isResourceLoaded("namespace:" + type.getName())) {
            String xmlResource = type.getName().replace('.', '/') + ".xml";
            // #1347
            InputStream inputStream = type.getResourceAsStream("/" + xmlResource);
            if (inputStream == null) {
                // Search XML mapper that is not in the module but in the classpath.
                try {
                    inputStream = Resources.getResourceAsStream(type.getClassLoader(), xmlResource);
                } catch (IOException e2) {
                    // ignore, resource is not required
                }
            }
            if (inputStream != null) {
                XMLMapperBuilder xmlParser = new XMLMapperBuilder(inputStream, assistant.getConfiguration(), xmlResource, configuration.getSqlFragments(), type.getName());
                xmlParser.parse();
            }
        }
    }

    // 解析缓存注解@CacheNamespace定义信息
    private void parseCache() {
        CacheNamespace cacheDomain = type.getAnnotation(CacheNamespace.class);
        if (cacheDomain != null) {
            Integer size = cacheDomain.size() == 0 ? null : cacheDomain.size();
            Long flushInterval = cacheDomain.flushInterval() == 0 ? null : cacheDomain.flushInterval();
            Properties props = convertToProperties(cacheDomain.properties());
            assistant.useNewCache(cacheDomain.implementation(), cacheDomain.eviction(), flushInterval, size, cacheDomain.readWrite(), cacheDomain.blocking(), props);
        }
    }
    // 解析缓存引用注解@CacheNamespaceRef定义信息
    private void parseCacheRef() {
        CacheNamespaceRef cacheDomainRef = type.getAnnotation(CacheNamespaceRef.class);
        if (cacheDomainRef != null) {
            Class<?> refType = cacheDomainRef.value();
            String refName = cacheDomainRef.name();
            if (refType == void.class && refName.isEmpty()) {
                throw new BuilderException("Should be specified either value() or name() attribute in the @CacheNamespaceRef");
            }
            if (refType != void.class && !refName.isEmpty()) {
                throw new BuilderException("Cannot use both value() and name() attribute in the @CacheNamespaceRef");
            }
            String namespace = (refType != void.class) ? refType.getName() : refName;
            try {
                assistant.useCacheRef(namespace);
            } catch (IncompleteElementException e) {
                configuration.addIncompleteCacheRef(new CacheRefResolver(assistant, namespace));
            }
        }
    }
}
```

`MapperAnnotationBuilder`类是用于`Mapper`注解构建的类，主要用于解析`Mapper`接口方法上的注解声明元信息，通过元信息构建`MappedStatement`实例。注解处理的主要逻辑如下：

1. 如果`Mapper`关联的xml文件（文件路径需要和`Mapper`接口包路径一样）没有加载，就优先处理。
2. 解析缓存的注解`@CacheNamespace`，注解声明在接口上。
3. 解析缓存引用的注解`@CacheNamespaceRef`，也是声明在接口上。
4. 接下来就是循环处理方法上面的注解，首先方法上存在`@Select`或`@SelectProvider`、`@ResultMap`注解时，会优先解析`ResultMap`的解析。
5. 接下来处理方法上的核心注解解析。这里可能会抛出`IncompleteElementException`，是延迟解析机制的一种场景，会将`MethodResolver`暂存到`Configuration`中的`incompleteMethods`中，待后续解析。

🌈 这里单独说一下方法主要注解的解析逻辑：

```java
public class MapperAnnotationBuilder {
    private static final Set<Class<? extends Annotation>> statementAnnotationTypes = Stream
    .of(Select.class, Update.class, Insert.class, Delete.class, SelectProvider.class, UpdateProvider.class,
        InsertProvider.class, DeleteProvider.class)
    .collect(Collectors.toSet());

    void parseStatement(Method method) {
        // 获取参数类型，当只有一个参数时，就是该参数类型，否则是ParamMap类型
        final Class<?> parameterTypeClass = getParameterType(method);
        // 获取语言驱动器，方法上@Lang注解指定
        final LanguageDriver languageDriver = getLanguageDriver(method);
        // 获取主要注解
        getAnnotationWrapper(method, true, statementAnnotationTypes).ifPresent(statementAnnotation -> {
            // 解析注解，得到SQL定义源对象
            final SqlSource sqlSource = buildSqlSource(statementAnnotation.getAnnotation(), parameterTypeClass, languageDriver, method);
            // 得到SQL命令类型
            final SqlCommandType sqlCommandType = statementAnnotation.getSqlCommandType();
            // 获取@Options注解的声明信息
            final Options options = getAnnotationWrapper(method, false, Options.class).map(x -> (Options)x.getAnnotation()).orElse(null);
            // 获取MappedStatement唯一标识：接口全名和方法名拼接
            final String mappedStatementId = type.getName() + "." + method.getName();
            final KeyGenerator keyGenerator;
            String keyProperty = null;
            String keyColumn = null;
            if (SqlCommandType.INSERT.equals(sqlCommandType) || SqlCommandType.UPDATE.equals(sqlCommandType)) {
                // 如果是插入和更新SQL的话，需要处理@SelectKey注解，来判断Key生成器
                SelectKey selectKey = getAnnotationWrapper(method, false, SelectKey.class).map(x -> (SelectKey)x.getAnnotation()).orElse(null);
                if (selectKey != null) {
                    keyGenerator = handleSelectKeyAnnotation(selectKey, mappedStatementId, getParameterType(method), languageDriver);
                    keyProperty = selectKey.keyProperty();
                } else if (options == null) {
                    keyGenerator = configuration.isUseGeneratedKeys() ? Jdbc3KeyGenerator.INSTANCE : NoKeyGenerator.INSTANCE;
                } else {
                    keyGenerator = options.useGeneratedKeys() ? Jdbc3KeyGenerator.INSTANCE : NoKeyGenerator.INSTANCE;
                    keyProperty = options.keyProperty();
                    keyColumn = options.keyColumn();
                }
            } else {
                keyGenerator = NoKeyGenerator.INSTANCE;
            }
            // 处理其他定义的信息
            Integer fetchSize = null;
            Integer timeout = null;
            StatementType statementType = StatementType.PREPARED;
            ResultSetType resultSetType = configuration.getDefaultResultSetType();
            boolean isSelect = sqlCommandType == SqlCommandType.SELECT;
            boolean flushCache = !isSelect;
            boolean useCache = isSelect;
            if (options != null) {
                if (FlushCachePolicy.TRUE.equals(options.flushCache())) {
                    flushCache = true;
                } else if (FlushCachePolicy.FALSE.equals(options.flushCache())) {
                    flushCache = false;
                }
                useCache = options.useCache();
                fetchSize = options.fetchSize() > -1 || options.fetchSize() == Integer.MIN_VALUE ? options.fetchSize() : null; //issue #348
                timeout = options.timeout() > -1 ? options.timeout() : null;
                statementType = options.statementType();
                if (options.resultSetType() != ResultSetType.DEFAULT) {
                    resultSetType = options.resultSetType();
                }
            }
            // 如果是查询SQL，解析方法上声明的ResultMap注解
            String resultMapId = null;
            if (isSelect) {
                ResultMap resultMapAnnotation = method.getAnnotation(ResultMap.class);
                if (resultMapAnnotation != null) {
                    resultMapId = String.join(",", resultMapAnnotation.value());
                } else {
                    resultMapId = generateResultMapName(method);
                }
            }
            // 使用MapperBuilderAssistant创建MappedStatement实例
            assistant.addMappedStatement(
                mappedStatementId,
                sqlSource,
                statementType,
                sqlCommandType,
                fetchSize,
                timeout,
                // ParameterMapID
                null,
                parameterTypeClass,
                resultMapId,
                getReturnType(method),
                resultSetType,
                flushCache,
                useCache,
                // TODO gcode issue #577
                false,
                keyGenerator,
                keyProperty,
                keyColumn,
                statementAnnotation.getDatabaseId(),
                languageDriver,
                // ResultSets
                options != null ? nullOrEmpty(options.resultSets()) : null);
        });
    }
}
```

其实这块逻辑和前面解析select、insert等标签章节是相似的，只不过元信息的获取逻辑从xml变成了注解，最终的目标都是使用`MapperBuilderAssistant`创建`MappedStatement`实例。

## MapperBuilderAssistant说明

该类主要负责在解析出xml信息后，使用这些参数调用相关的建造者模式类去实例化对象。支持创建的对象包括：

+ Cache：解析`<cache/>`标签，创建缓存声明实例。
+ ParameterMapping：解析`<update/>`、`<insert/>`、`<delete/>`标签的`parameterMap`属性。
+ Discriminator：解析`<resultMap/>`标签的子标签`<discriminator/>`，生成鉴别器对象。
+ <font style="color:#F38F39;">MappedStatement</font>：解析四种SQL定义标签生成的核心对象，存储在`Configuration`实例中。
+ ResultMapping：解析`<resultMap/>`标签生成的对象，用于处理高级结果映射。
+ ParameterMap：解析标签内的`parameterMap`属性生成的对象，存储在当前`MappedStatement`对象。
+ ResultMap：解析标签内的`resultMap`属性生成的对象，存储在当前`MappedStatement`对象。

```java
public class MapperBuilderAssistant extends BaseBuilder {
    public MappedStatement addMappedStatement(
        String id,
        SqlSource sqlSource,
        StatementType statementType,
        SqlCommandType sqlCommandType,
        Integer fetchSize,
        Integer timeout,
        String parameterMap,
        Class<?> parameterType,
        String resultMap,
        Class<?> resultType,
        ResultSetType resultSetType,
        boolean flushCache,
        boolean useCache,
        boolean resultOrdered,
        KeyGenerator keyGenerator,
        String keyProperty,
        String keyColumn,
        String databaseId,
        LanguageDriver lang,
        String resultSets) {
        // 这里如果需要且还没有解析cache-ref标签的话，就抛出异常中断后续处理，等后面延迟加载
        if (unresolvedCacheRef) {
            throw new IncompleteElementException("Cache-ref not yet resolved");
        }

        id = applyCurrentNamespace(id, false);
        boolean isSelect = sqlCommandType == SqlCommandType.SELECT;
        // 创建MappedStatement内部的建造者类实例
        MappedStatement.Builder statementBuilder = new MappedStatement.Builder(configuration, id, sqlSource, sqlCommandType)
        .resource(resource)
        .fetchSize(fetchSize)
        .timeout(timeout)
        .statementType(statementType)
        .keyGenerator(keyGenerator)
        .keyProperty(keyProperty)
        .keyColumn(keyColumn)
        .databaseId(databaseId)
        .lang(lang)
        .resultOrdered(resultOrdered)
        .resultSets(resultSets)
        .resultMaps(getStatementResultMaps(resultMap, resultType, id))
        .resultSetType(resultSetType)
        .flushCacheRequired(valueOrDefault(flushCache, !isSelect))
        .useCache(valueOrDefault(useCache, isSelect))
        .cache(currentCache);

        ParameterMap statementParameterMap = getStatementParameterMap(parameterMap, parameterType, id);
        if (statementParameterMap != null) {
            statementBuilder.parameterMap(statementParameterMap);
        }
        // 创建MappedStatement对象并注册到Configuration中
        MappedStatement statement = statementBuilder.build();
        configuration.addMappedStatement(statement);
        return statement;
    }
}
```

这里就是通过一个建造者模式类来构建`MappedStatement`，并注册到`Configuration`中，这样就完成了一个基础的SQL定义的解析。

其中`unresolvedCacheRef`标识了是否未解析`<cache-ref/>`标签关于缓存引用的定义，<font style="color:rgb(73, 246, 181);">因为这里是对于其他Mapper xml文件中定义缓存的引用，所以需要等到所有xml文件解析完毕才能完全解析，所以这里抛异常走延迟解析的机制</font>（🚀延迟解析时机在`Configuration.getMappedStatement(id, validateIncompleteStatements)`中）。

## Mapper定义元素和组件关联关系
其中标签元素是在Mapper xml文件中定义的，而注解除了`@CacheNamespace`和`@CacheNamespaceRef`是在`Mapper`接口上声明的，其他注解都是在接口中的方法上声明的。

| 元素 | 对应组件 | 说明 |
| --- | --- | --- |
| `<insert/>`、`<update/>`、`<delete/>`、`<select/>` | `MappedStatement` | Mapper xml文件中的这四类标签都会被解析构建成`MappedStatement`实例，用来代表一个SQL语句的定义。 |
| `@Select`、`@Update`、`@Insert`、`@Delete`、`@SelectProvider`、`@UpdateProvider`、`@InsertProvider`、`@DeleteProvider` | | |
| `<cache/>` | `Cache`<br/> | 通过`namespace`为键存储在`Configuration`中。<br/>`<cache/>`是用来定义`Cache`实例。<br/>而`<cache-ref/>`是用来关联一个已存在的`Cache`实例。 |
| `@CacheNamespace` | | |
| `<cache-ref/>` | | |
| `@CacheNamespaceRef` | | |
| `<parameterMap/>` | `ParameterMap` | 通过标签属性`id`定义唯一标识，存储在`Configuration`中。<font style="background-color:#E4495B;">官方已经弃用</font> |
| `<resultMap/>` | `ResultMap` | 该标签以及它的三个子级标签都会构建成为`ResultMap`实例，这是很常用的结果映射定义对象。通过标签属性`id`定义唯一标识，存储在`Configuration`中。 |
| 子标签`<association/>` | | |
| 子标签`<collection/>` | | |
| 子标签`<discriminator/case/>` | | |
| `@Case` | | |
| `<resultMap/>`子标签：`<id/>`、`<result/>` | `ResultMapping` | 这是表示结果映射中的具体映射规则的对象，是`ResultMap`的成员变量，一个结果映射包含多个具体映射规则。<br/>`<constructor/>`子标签的`ResultMapping`会设置特殊标识`ResultFlag`。 |
| `<constructor/>`子标签`<idArg/>`、`<arg/>` | | |
| `@Arg`、`@Result` | | |
| 子标签`<discriminator/>` | `Discriminator` | 是`ResultMap`的成员变量，构建鉴别器对实例 |
| `@TypeDiscriminator` | | |

## 延迟解析机制

前文多次提到了延迟解析机制，该机制是在构建`MappedStatement`实例以及其他定义信息起作用，要解决的问题总结就是：<font style="color:#F38F39;">当前xml文件中定义的信息存在依赖于本文件内后续的信息或者其他xml文件中定义的信息（包含此刻未解析到的），需要等到依赖的信息全部解析完成之后再次触发解析当前信息</font>。

### 代码说明

```java
public class Configuration {
    protected final Map<String, MappedStatement> mappedStatements = new StrictMap<MappedStatement>("Mapped Statements collection")
    .conflictMessageProducer((savedValue, targetValue) ->
                             ". please check " + savedValue.getResource() + " and " + targetValue.getResource());
    protected final Collection<XMLStatementBuilder> incompleteStatements = new LinkedList<>();
    protected final Collection<CacheRefResolver> incompleteCacheRefs = new LinkedList<>();
    protected final Collection<ResultMapResolver> incompleteResultMaps = new LinkedList<>();
    protected final Collection<MethodResolver> incompleteMethods = new LinkedList<>();

    public void addMappedStatement(MappedStatement ms) {
        mappedStatements.put(ms.getId(), ms);
    }

    public Collection<String> getMappedStatementNames() {
        buildAllStatements();
        return mappedStatements.keySet();
    }

    public Collection<MappedStatement> getMappedStatements() {
        buildAllStatements();
        return mappedStatements.values();
    }

    public MappedStatement getMappedStatement(String id) {
        return this.getMappedStatement(id, true);
    }

    public MappedStatement getMappedStatement(String id, boolean validateIncompleteStatements) {
        // 是否需要校验未完成解析的Statement
        if (validateIncompleteStatements) {
            // 构建完成的Statement实例
            buildAllStatements();
        }
        return mappedStatements.get(id);
    }

    protected void buildAllStatements() {
        // 解析ResultMap
        parsePendingResultMaps();
        if (!incompleteCacheRefs.isEmpty()) {
            // 解析未完成的缓存引用
            synchronized (incompleteCacheRefs) {
                incompleteCacheRefs.removeIf(x -> x.resolveCacheRef() != null);
            }
        }
        if (!incompleteStatements.isEmpty()) {
            // 解析未完成的MappedStatement
            synchronized (incompleteStatements) {
                incompleteStatements.removeIf(x -> {
                    x.parseStatementNode();
                    return true;
                });
            }
        }
        if (!incompleteMethods.isEmpty()) {
            // 解析未完成的方法，这个主要是支持Mapper接口方法注解的处理
            synchronized (incompleteMethods) {
                incompleteMethods.removeIf(x -> {
                    x.resolve();
                    return true;
                });
            }
        }
    }

    private void parsePendingResultMaps() {
        if (incompleteResultMaps.isEmpty()) {
            return;
        }
        synchronized (incompleteResultMaps) {
            boolean resolved;
            IncompleteElementException ex = null;
            do {
                resolved = false;
                Iterator<ResultMapResolver> iterator = incompleteResultMaps.iterator();
                while (iterator.hasNext()) {
                    try {
                        iterator.next().resolve();
                        iterator.remove();
                        resolved = true;
                    } catch (IncompleteElementException e) {
                        ex = e;
                    }
                }
            } while (resolved);
            if (!incompleteResultMaps.isEmpty() && ex != null) {
                // At least one result map is unresolvable.
                throw ex;
            }
        }
    }
}
```

`Configuration`对象中使用四个成员变量存储未完成解析的信息（在前面章节的代码中存在添加逻辑），这也说明了这四部分是支持延迟解析机制的：

+ `incompleteStatements`：存储的是未完成解析的`XMLStatementBuilder`集合，前文学习的用来构建`MappedStatement`的对象。
+ `incompleteCacheRefs`：存储的是未完成解析的缓存引用解析器`CacheRefResolver`集合
+ `incompleteResultMaps`：存储的是未完成解析的`ResultMapResolver`集合
+ `incompleteMethods`：存储的是未完成解析的`MethodResolver`集合，主要用来解析`Mapper`接口方法注解。

### 🌈实现说明

当存在引用元素的问题（且找不到已解析的引用实例时）：

1. 通过抛出`IncompleteElementException`异常（见上文抛出该异常的代码），中断解析当前解析；
2. 并将具体的解析信息对象暂存到`Configuration`中（见前一节的说明的四个成员变量）；
3. 待后续解析完所有依赖的元素后再次解析当前暂存的内容。
   1. 当前Mapper xml文件解析完成后，尝试触发解析一次`ResultMap`、`CacheRef`、`Statement`三种内容。
   2. 当所有解析完成后，再首次获取MappedStatement实例时（常见于`SqlSession`中执行SQL的方法中），会触发再次解析暂存的四部分内容：`ResultMap`、`CacheRef`、`Statement`、`Mapper`接口方法注解，这四部分内容。

## 流程总结

下图是解析Mapper SQl定义解析整个过程中，主要参与的组件类以及主要的逻辑方法。

![Mapper SQL解析过程](http://file.zhangdx.cn/article/166/IMG_ZHANGDX_20260112162151.jpeg)

主要参与解析过程的就两个入口：

1. 解析Mapper xml文件入口，在`XMLMapperBuilder.parse()`方法中，包含了大部分解析逻辑。
2. 获取`MappedStatement`实例入口，在`Configuration.getMappedStatement()`等方法（常见于`SqlSession`中执行SQL的方法中），只包含需要延迟解析的部分内容。

主要参与的组件：

1. XMLMapperBuilder：解析Mapper xml文件的核心类。
2. MapperAnnotationBuilder：解析`Mapper`接口方法注解的核心类。
3. MapperBuilderAssistant：负责`Mapper`相关运行时实例创建的助理类，通过解析后的信息作为参数创建。
4. Configuration：MyBatis框架运行时核心配置类，存储解析产生的一些`Mapper`相关实例。
5. MapperRegistry：注册`Mapper`接口的注册器，也是注解解析的入口类。
6. XMLStatementBuilder：专门负责解析Mapper xml文件中`Statement`相关标签（定义增删改查四种类型SQL语句的标签，同时需要产生`MappedStatement`实例）的类。
7. CacheRefResolver：负责解析缓存引用信息的类。
8. ResultMapResolver：负责解析`ResultMap`高级结果映射定义信息的类。
9. MethodResolver：负责解析Mapper接口方法的类，只在延迟解析中起作用。

