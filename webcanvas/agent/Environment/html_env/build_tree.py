import copy
import re
import cssutils
from collections import deque
from lxml.html import etree
from io import StringIO

from .utils import ElementNode, TagNameList, MapTagNameList, stringfy_selector
from .active_elements import ActiveElements
import logging
cssutils.log.setLevel(logging.CRITICAL)

class HTMLTree:
    def __init__(self):
        self.elementNodes = [ElementNode] * 100000
        self.rawNode2id: dict = {}
        self.element2id: dict = {}
        self.id2rawNode: dict = {}
        self.valid: list[bool] = [False] * 100000
        self.nodeCounts: int
        self.nodeDict = {}
        self.element_value = {}
        self.invisible_styles=[]
    
    def fetch_html_content(self, html_content) -> str:
        """
        Fetch the html content to extract and prune the DOM tree based on visibility styles.
        """
        self.__init__()
        parser = etree.HTMLParser()
        self.tree = etree.parse(StringIO(html_content), parser)
        self.copy_tree = copy.deepcopy(self.tree)
        root = self.tree.getroot()

        # Extract the visibility styles from the head tag
        head=self.tree.find(".//head")
        if len(head)>0:
            # Extract the style tags in the head tag
            styles=head.findall(".//style")
            if len(styles)>0:
                for style in styles:
                    css_content=style.text
                    if not css_content:
                        continue

                    # Parse the css content
                    sheet=cssutils.parseString(css_content)
                    for rule in sheet:
                        if rule.type==rule.STYLE_RULE:
                            visibility=rule.style.getPropertyValue("visibility")
                            display=rule.style.getPropertyValue("display")
                            if visibility=="hidden" or display=="none":
                                self.invisible_styles.append(rule.selectorText)
        self.init_html_tree(root)
        self.build_html_tree(root)
        # print(self.invisible_styles)
        return self.prune_tree()
    
    @staticmethod
    def build_node(node, idx: int) -> ElementNode:
        elementNode = ElementNode()
        elementNode["nodeId"] = idx
        elementNode["tagName"] = node.tag
        elementNode["text"] = node.text if node.text else ""
        elementNode["attributes"] = node.attrib
        elementNode["childIds"] = []
        elementNode["parentId"] = ""
        elementNode["siblingId"] = ""
        elementNode["twinId"] = ""
        elementNode["depth"] = 1
        elementNode["htmlContents"] = etree.tostring(
            node, pretty_print=True).decode()
        return elementNode

    def build_mapping(self) -> None:
        self.element2id = {value["nodeId"]: index for index,
                           value in enumerate(self.elementNodes)}
        self.id2rawNode = {str(index): value for value,
                           index in self.rawNode2id.items()}

    def init_html_tree(self, root) -> None:
        node_queue = deque([root])
        node_id = 0
        while node_queue:
            node = node_queue.popleft()
            self.elementNodes[node_id] = HTMLTree().build_node(node, node_id)
            self.rawNode2id[node] = node_id
            node_id += 1
            for child in node.getchildren():
                node_queue.append(child)
        self.build_mapping()
        self.nodeCounts = node_id
        self.valid = self.valid[:self.nodeCounts + 1]

    def build_html_tree(self, root) -> None:
        node_queue = deque([root])
        root_id = self.rawNode2id[root]
        self.elementNodes[root_id]["parentId"] = -1
        while node_queue:
            node = node_queue.popleft()
            parent_id = self.rawNode2id[node]
            tag_st = {}
            sibling_id = 1
            for child in node.getchildren():
                child_id = self.rawNode2id[child]
                tag_name = self.elementNodes[child_id].get("tagName")
                tag_st[tag_name] = tag_st.get(tag_name, 0) + 1
                twin_id = tag_st.get(tag_name)
                self.elementNodes[parent_id]["childIds"].append(child_id)
                self.elementNodes[child_id]["parentId"] = parent_id
                self.elementNodes[child_id]["twinId"] = twin_id
                self.elementNodes[child_id]["depth"] = self.elementNodes[parent_id]["depth"] + 1
                self.elementNodes[child_id]["siblingId"] = sibling_id
                node_queue.append(child)
                sibling_id += 1
        self.pruningTreeNode = copy.deepcopy(self.elementNodes)

    def get_xpath(self, idx: int) -> str:
        locator_str = ""
        current_node = self.elementNodes[idx]
        tag_name = current_node["tagName"]
        twinId = current_node["twinId"]
        locator_str = "/" + tag_name + "[" + str(twinId) + "]"
        while current_node["parentId"] != 0:
            parentid = current_node["parentId"]
            current_node = self.elementNodes[parentid]
            current_tag_name = current_node["tagName"]
            twinId = current_node["twinId"]
            locator_str = "/" + current_tag_name + \
                "[" + str(twinId) + "]" + locator_str
        parentid = current_node["parentId"]
        current_node = self.elementNodes[parentid]
        current_tag_name = current_node["tagName"]
        return "/" + current_tag_name + locator_str

    def get_selector(self, idx: int) -> str:
        selector_str = ""
        current_node = self.elementNodes[idx]
        while current_node["parentId"] != -1:
            tag_name = current_node["tagName"]
            siblingId = str(current_node["siblingId"])
            if current_node["attributes"].get('id'):
                current_selector = stringfy_selector(
                    current_node["attributes"].get('id'))
                return "#" + current_selector + selector_str
            if len(self.elementNodes[current_node["parentId"]]["childIds"]) > 1:
                uu_twin_node = True
                uu_id = True
                for childId in self.elementNodes[current_node["parentId"]]["childIds"]:
                    sib_node = self.elementNodes[childId]
                    if sib_node["nodeId"] != current_node["nodeId"] and current_node["attributes"].get('class') and sib_node["attributes"].get("class") == current_node["attributes"].get('class'):
                        uu_twin_node = False
                    if sib_node["nodeId"] != current_node["nodeId"] and current_node["tagName"] == sib_node["tagName"]:
                        uu_id = False
                if uu_id:
                    selector_str = " > " + tag_name + selector_str
                elif current_node["attributes"].get('class') and uu_twin_node is True:
                    # fix div.IbBox.Whs\(n\)
                    selector_str = " > " + tag_name + "." + \
                        stringfy_selector(
                            current_node["attributes"].get('class')) + selector_str
                else:
                    selector_str = " > " + tag_name + \
                        ":nth-child(" + siblingId + ")" + selector_str
            else:
                selector_str = " > " + tag_name + selector_str
            current_node = self.elementNodes[current_node["parentId"]]
        return current_node["tagName"] + selector_str

    def is_valid(self, idx: int) -> bool:
        node = self.pruningTreeNode[idx]
        if node["tagName"] in TagNameList:
            return ActiveElements.is_valid_element(node)
        return False

    def prune_tree(self) -> str:
        """Traverse each element to determine if it is valid and prune"""
        result_list = []
        root = self.pruningTreeNode[0]
        if root is None:
            result_list = []
        stack = [root]
        while stack:
            node = stack.pop()
            nodeId = node["nodeId"]
            result_list.append(nodeId)
            children = []
            for childId in node["childIds"]:
                childNode = self.pruningTreeNode[childId]
                children.append(childNode)
            stack.extend(children)
        result = result_list[::-1]
        for nodeId in result:
            if self.is_valid(nodeId) or self.valid[nodeId] is True:
                rawNode = self.id2rawNode[str(nodeId)]
                html_contents = etree.tostring(
                    rawNode, pretty_print=True).decode()
                self.pruningTreeNode[nodeId]["htmlContents"] = html_contents
                self.valid[nodeId] = True
                current_id = nodeId
                while self.pruningTreeNode[current_id]["parentId"] != -1:
                    parent_id = self.pruningTreeNode[current_id]["parentId"]
                    self.valid[parent_id] = True
                    current_id = parent_id
            else:
                rawNode = self.id2rawNode[str(nodeId)]
                rawNode.getparent().remove(rawNode)
                current_node = self.pruningTreeNode[nodeId]
                current_node["htmlContents"] = ""
                parentid = current_node["parentId"]
                # self.pruningTreeNode[parentid]["childIds"].remove(nodeId)
                for child in self.pruningTreeNode[parentid]["childIds"]:
                    child_node=self.pruningTreeNode[child]
                    self.set_invalid(child_node)
                # self.valid[nodeId] = False
        return self.pruningTreeNode[0]["htmlContents"]

    def get_element_contents(self, idx: int) -> str:
        node = self.elementNodes[idx]
        html_content = node["htmlContents"]
        return html_content
    
    def get_tag_name(self, element: ElementNode) -> (str, int, str):  # type: ignore
        tag_name = ActiveElements.get_element_tagName(element)
        tag_idx = element["nodeId"]
        validContent=HTMLTree().process_element_contents(element)
        
        if tag_name == "span":
            parent_id = element["parentId"]
            if parent_id != -1: 
                parent_node = self.pruningTreeNode[parent_id]
                parent_tag_name, parent_tag_idx, _ = self.get_tag_name(parent_node)
                for sibling_id in parent_node["childIds"]:
                    sibling_node = self.pruningTreeNode[sibling_id]
                    if sibling_node["tagName"] == "span" and sibling_id!=element["nodeId"]:
                        validContent += HTMLTree().process_element_contents(sibling_node)
                        self.set_invalid(sibling_node)  # Mark sibling as invalid
            return parent_tag_name, tag_idx, validContent

        if tag_name == "unknown":
            tag_name = element["tagName"]
            tag_idx = element["nodeId"]
            validContent=HTMLTree().process_element_contents(element)
            if tag_name in MapTagNameList:
                parent_node = self.pruningTreeNode[element["parentId"]]
                parent_tag_name, parent_tag_idx, _ = self.get_tag_name(parent_node)
                return parent_tag_name, tag_idx, validContent
            else:
                return ("statictext", tag_idx, validContent)
        return (tag_name, tag_idx, validContent)

    def set_invalid(self, node: ElementNode) -> None:
        node_id = node["nodeId"]
        self.valid[node_id] = False
        node["htmlContents"] = "" 

    def set_invalid_children(self, node: ElementNode) -> None:
        for child_id in node["childIds"]:
            child_node = self.pruningTreeNode[child_id]
            self.set_invalid_children(child_node) 

    def build_dom_tree(self) -> str:
        root = self.pruningTreeNode[0]
        stack = [(root, 0)]
        contents = ""
        num = 0
        dropdown_process = True
        effective_depths = {}
        last_content_depth = -1

        def calculate_specificity(selector):
            """
            Calculate CSS selector specificity as a tuple (id_count, class_count, tag_count).
            """
            id_count = selector.count("#")
            class_count = selector.count(".")
            tag_count = len([part for part in selector.split() if part.isalnum()])
            return id_count, class_count, tag_count

        def matches_selector(selector, node):
            """
            Enhanced function to check if a node matches a selector.
            Supports ID (#id), class (.class), and tag selectors.
            """
            node_id = node["attributes"].get("id", "")
            node_classes = node["attributes"].get("class", "").split()
            node_tag = node["tagName"]

            # Handle ID and class selectors
            if "#" in selector and "." in selector:
                id_part, class_part = selector.split(".")
                return node_id == id_part[1:] and class_part in node_classes
            
            # Handle ID selectors
            if selector.startswith("#"):
                return node_id == selector[1:]
            # Handle class selectors
            elif selector.startswith("."):
                return selector[1:] in node_classes
            # Handle tag selectors
            else:
                return node_tag == selector

        def matches_complex_selector(selector, node):
            """
            Check if a node matches a complex selector with combinators (e.g., .parent .child).
            Currently supports:
            - Descendant combinators (e.g., `.parent .child`)
            """
            parts = selector.split()
            current_node = node
            for part in reversed(parts):
                if not matches_selector(part, current_node):
                    return False
                # Move to the parent node for the next part
                parent_id = current_node["parentId"]
                if parent_id == -1:
                    return False
                current_node = self.pruningTreeNode[parent_id]
            return True

        def get_applicable_styles(node):
            """
            Retrieve all applicable styles for a node, considering specificity and order.
            """
            applicable_styles = []
            for style in self.invisible_styles:
                specificity = calculate_specificity(style)
                if matches_complex_selector(style, node):
                    applicable_styles.append((specificity, style))
            # Sort styles by specificity and then by their order of appearance
            applicable_styles.sort(key=lambda x: x[0], reverse=True)
            return [style for _, style in applicable_styles]

        while stack:
            node, actual_depth = stack.pop()

            # Check if the node matches any styles in invisible_styles
            if get_applicable_styles(node):
                self.set_invalid(node)
                self.set_invalid_children(node)
                continue

            # Check if the node is visible
            if not ActiveElements.is_visiable(node):
                for child in node["childIds"]:
                    child_node = self.pruningTreeNode[child]
                    self.set_invalid(child_node)
                continue

            node_has_content = False
            if self.valid[node["nodeId"]]:
                tag_name, tag_idx, validContent = self.get_tag_name(node)
                content_text = validContent if validContent else HTMLTree().process_element_contents(node)
                num += 1
                self.nodeDict[num] = tag_idx
                if "selected" in node["attributes"]:
                    continue

                # Process the node attributes
                attributes = []
                expanded = node["attributes"].get("aria-expanded")
                if expanded:
                    attributes.append(f" expanded: {expanded}")
                attr_class = node["attributes"].get("class")
                if attr_class:
                    if "dropdown-menu" in attr_class and not dropdown_process:
                        dropdown_process = True
                        continue
                    elif "dropdown" in attr_class and "open" not in attr_class:
                        dropdown_process = False
                has_popup = node["attributes"].get("aria-haspopup")
                if has_popup:
                    attributes.append(f" hasPopup: {has_popup}")
                focused = node["attributes"].get("focused")
                if focused:
                    attributes.append(f" focused: {focused}")
                attributes_text = " ".join(attributes)

                # Process the node content
                if node_has_content or re.search(r'[a-zA-Z0-9]', content_text):
                    if actual_depth not in effective_depths:
                        effective_depths[actual_depth] = effective_depths[last_content_depth] + 1 if last_content_depth in effective_depths else 0
                        last_content_depth = actual_depth
                    effective_indent_level = effective_depths[actual_depth]
                    contents += "  " * effective_indent_level + "[" + str(num) + "] " + tag_name + \
                                " " + f"'{content_text}'" + attributes_text + "\n"
                    self.element_value[str(tag_idx)] = content_text
                    node_has_content = True
                    # If the node is not expanded, skip it
                    if expanded == "false":
                        continue
            if node_has_content and (actual_depth + 1) not in effective_depths:
                effective_depths[actual_depth + 1] = effective_indent_level + 1
            children = [self.pruningTreeNode[child_id] for child_id in node["childIds"]]
            for child in reversed(children):
                stack.append((child, actual_depth + 1))
        return contents
    
    def get_selector_and_xpath(self, idx: int) -> (str, str):  # type: ignore
        try:
            selector = self.get_selector(idx)
            xpath = self.get_xpath(idx)
            return selector, xpath
        except:
            print(f"can't locate element")

    @staticmethod
    def process_element_contents(element: ElementNode) -> str:
        # TODO Add appropriate interactive element information, currently only processing interactive elements with text attributes
        html_text = ActiveElements.get_element_value(element)
        if html_text is None:
            return ""
        return html_text.replace("\n", "").replace("\t", "").strip()

    def get_element_value(self, element_id: int) -> str:
        return self.element_value[str(element_id)]


__all__ = [
    "HTMLTree"
]
